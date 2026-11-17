"""Fetch, hash, and archive Moodle PDF versions without changing old files."""

from __future__ import annotations

import hashlib
import asyncio
import io
import json
import os
import re
import tempfile
import time
from datetime import datetime, timezone
from html.parser import HTMLParser
from http.cookiejar import Cookie, CookieJar, DefaultCookiePolicy
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, unquote_to_bytes, urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, HTTPCookieProcessor, ProxyHandler, Request, build_opener


MAX_WRAPPER_BYTES = 2 * 1024 * 1024
MAX_REDIRECTS = 5
REDIRECT_STATUSES = {301, 302, 303, 307, 308}
READ_CHUNK_BYTES = 64 * 1024
COURSE_ID_RE = re.compile(r"^[A-Z0-9_-]{2,16}$")


class PdfFetchError(RuntimeError):
    """A Moodle resource could not be safely downloaded as a PDF."""


class PdfArchiveError(RuntimeError):
    """A version/catalog path is invalid or its history is inconsistent."""


def _authorized_url(url: str, *, allow_pluginfile: bool = True) -> str:
    if not isinstance(url, str) or not url:
        raise PdfFetchError("URL vide ou invalide")
    parsed = urlsplit(url)
    if (parsed.scheme.lower() != "https" or parsed.hostname != "curs.upb.ro"
            or parsed.username is not None or parsed.password is not None
            or parsed.port not in (None, 443) or parsed.fragment):
        raise PdfFetchError("Seules les URL HTTPS curs.upb.ro sont autorisées")
    sensitive_names = {"token", "wstoken", "sesskey", "password", "passwd", "secret", "authorization", "session", "sid", "key"}
    if any(name.lower() in sensitive_names for name, _ in parse_qsl(parsed.query, keep_blank_values=True)):
        raise PdfFetchError("Les URL Moodle contenant des paramètres d'authentification sont refusées")
    in_course = parsed.path == "/2026" or parsed.path.startswith("/2026/")
    is_pluginfile = bool(re.match(r"^/(?:2026/)?pluginfile\.php(?:/|$)", parsed.path))
    if not in_course and not (allow_pluginfile and is_pluginfile):
        raise PdfFetchError("URL hors du cours 2026 ou du chemin Moodle pluginfile.php")
    return url


def _header_map(response) -> dict[str, str]:
    headers = getattr(response, "headers", {}) or {}
    return {str(key).lower(): str(value) for key, value in headers.items()}


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _moodle_cookie(value):
    """Copy only the dedicated context's Moodle cookies into an in-memory jar."""
    if not isinstance(value, dict):
        return None
    domain = str(value.get("domain", ""))
    name, content = value.get("name"), value.get("value")
    path = value.get("path", "/")
    if (domain.lstrip(".").lower() != "curs.upb.ro"
            or not isinstance(name, str) or not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", name)
            or not isinstance(content, str) or any(c in content for c in "\r\n")
            or not isinstance(path, str) or not path.startswith("/") or any(c in path for c in "\r\n")):
        return None
    expires = value.get("expires", -1)
    if not isinstance(expires, (int, float)):
        return None
    expires = int(expires) if expires > 0 else None
    if expires is not None and expires <= time.time():
        return None
    rest = {}
    if value.get("httpOnly"):
        rest["HttpOnly"] = None
    if value.get("sameSite") in {"Strict", "Lax", "None"}:
        rest["SameSite"] = value["sameSite"]
    return Cookie(0, name, content, None, False, domain.lower(), domain.startswith("."),
                  domain.startswith("."), path, True, bool(value.get("secure")), expires,
                  expires is None, None, None, rest, False)


def _browser_cookie(cookie):
    value = {"name": cookie.name, "value": cookie.value, "domain": cookie.domain,
             "path": cookie.path, "secure": cookie.secure,
             "httpOnly": cookie.has_nonstandard_attr("HttpOnly") or cookie.has_nonstandard_attr("httponly"),
             "expires": cookie.expires if cookie.expires is not None else -1}
    same_site = cookie.get_nonstandard_attr("SameSite", cookie.get_nonstandard_attr("samesite"))
    if same_site in {"Strict", "Lax", "None"}:
        value["sameSite"] = same_site
    return value


class _BoundedMoodleRequest:
    """Streaming GET client; never use Playwright's fully buffered APIResponse."""

    def __init__(self, cookies):
        self.jar = CookieJar(DefaultCookiePolicy(
            allowed_domains=("curs.upb.ro", ".curs.upb.ro"),
            strict_ns_domain=DefaultCookiePolicy.DomainStrictNonDomain))
        for raw in cookies:
            cookie = _moodle_cookie(raw)
            if cookie is not None:
                self.jar.set_cookie(cookie)
        self.initial = self._cookies()
        # Do not silently inherit a proxy from the operator's environment.
        self.opener = build_opener(ProxyHandler({}), _NoRedirect(), HTTPCookieProcessor(self.jar))

    def _cookies(self):
        return {(c.domain, c.path, c.name): _browser_cookie(c) for c in self.jar
                if c.domain.lstrip(".").lower() == "curs.upb.ro" and not c.is_expired()}

    def cookie_updates(self):
        current = self._cookies()
        updates = [value for key, value in current.items() if self.initial.get(key) != value]
        for key, previous in self.initial.items():
            if key not in current:
                updates.append({**previous, "value": "", "expires": 1})
        return updates

    def get_bounded(self, url, *, timeout, max_bytes):
        current_url = _authorized_url(url)
        deadline = time.monotonic() + timeout / 1000
        for redirect_count in range(MAX_REDIRECTS + 1):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise PdfFetchError("Délai de téléchargement Moodle dépassé")
            request = Request(current_url, method="GET", headers={
                "Cache-Control": "no-cache", "Pragma": "no-cache", "Accept-Encoding": "identity"})
            try:
                try:
                    response = self.opener.open(request, timeout=remaining)
                except HTTPError as error:
                    response = error  # A redirect/error is still a response to close.
            except (OSError, URLError) as error:
                raise PdfFetchError("Téléchargement Moodle indisponible") from error
            try:
                headers = _header_map(response)
                status = int(getattr(response, "status", getattr(response, "code", 0)))
                final_url = _authorized_url(response.geturl())
                if final_url != current_url:
                    raise PdfFetchError("Le transport Moodle a suivi une redirection implicitement")
                if status in REDIRECT_STATUSES:
                    location = headers.get("location")
                    if not location:
                        raise PdfFetchError(f"Redirection Moodle HTTP {status} sans Location")
                    if redirect_count == MAX_REDIRECTS:
                        raise PdfFetchError("Trop de redirections Moodle")
                    current_url = _authorized_url(urljoin(current_url, location))
                    continue
                if not 200 <= status < 300:
                    raise PdfFetchError(f"Réponse Moodle HTTP {status}")
                kind = headers.get("content-type", "").split(";", 1)[0].strip().lower()
                limit = min(max_bytes, MAX_WRAPPER_BYTES) if kind == "text/html" else max_bytes
                try:
                    declared = int(headers.get("content-length", ""))
                except ValueError:
                    declared = None
                if declared is not None and declared > limit:
                    raise PdfFetchError(f"Réponse supérieure à la limite de {limit} octets")
                body = bytearray()
                while True:
                    if time.monotonic() >= deadline:
                        raise PdfFetchError("Délai de téléchargement Moodle dépassé")
                    # At most one byte beyond the limit is requested, even when
                    # Content-Length is absent, false, or the server keeps sending.
                    chunk = response.read(min(READ_CHUNK_BYTES, limit - len(body) + 1))
                    if time.monotonic() >= deadline:
                        raise PdfFetchError("Délai de téléchargement Moodle dépassé")
                    if not chunk:
                        return bytes(body), headers, final_url
                    if len(body) + len(chunk) > limit:
                        raise PdfFetchError(f"Réponse supérieure à la limite de {limit} octets")
                    body.extend(chunk)
            except (OSError, URLError) as error:
                raise PdfFetchError("Lecture du téléchargement Moodle interrompue") from error
            finally:
                response.close()
        raise PdfFetchError("Redirections Moodle non résolues")


async def _request_no_redirects(request, url: str, *, timeout: int):
    """Request a URL while leaving redirects visible for per-hop validation."""
    return await request.get(url, timeout=timeout, max_redirects=0,
                             headers={"Cache-Control": "no-cache", "Pragma": "no-cache"})


async def _get_bounded(request, url: str, *, timeout: int, max_bytes: int):
    if isinstance(request, _BoundedMoodleRequest):
        # Cancelling the await does not terminate this worker thread. Its socket
        # timeout bounds a blocking read; the total deadline is also checked
        # before and after each read, and the response is closed in finally.
        return await asyncio.to_thread(request.get_bounded, url, timeout=timeout, max_bytes=max_bytes)
    # Compatibility adapter for existing lightweight request doubles. A real
    # APIRequestContext is converted to _BoundedMoodleRequest by fetch_pdf.
    current_url = _authorized_url(url)
    for redirect_count in range(MAX_REDIRECTS + 1):
        response = await _request_no_redirects(request, current_url, timeout=timeout)
        headers = _header_map(response)
        status = int(getattr(response, "status", 0))
        if status in REDIRECT_STATUSES:
            location = headers.get("location")
            await response.dispose()
            if not location:
                raise PdfFetchError(f"Redirection Moodle HTTP {status} sans Location")
            if redirect_count == MAX_REDIRECTS:
                raise PdfFetchError("Trop de redirections Moodle")
            current_url = _authorized_url(urljoin(current_url, location))
            continue
        try:
            if not 200 <= status < 300:
                raise PdfFetchError(f"Réponse Moodle HTTP {status}")
            content_type = headers.get("content-type", "").split(";", 1)[0].strip().lower()
            body_limit = min(max_bytes, MAX_WRAPPER_BYTES) if content_type == "text/html" else max_bytes
            declared_length = headers.get("content-length")
            if declared_length:
                try:
                    if int(declared_length) > body_limit:
                        raise PdfFetchError(f"Réponse supérieure à la limite de {body_limit} octets")
                except ValueError:
                    pass
            body = await response.body()
            if len(body) > body_limit:
                raise PdfFetchError(f"Réponse supérieure à la limite de {body_limit} octets")
            final_url = _authorized_url(getattr(response, "url", current_url))
            return body, headers, final_url
        finally:
            await response.dispose()
    raise PdfFetchError("Redirections Moodle non résolues")


class _MoodleFileLinkParser(HTMLParser):
    """Collect Moodle file URLs exposed by common resource wrapper elements."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "object":
            value = attrs.get("data")
        elif tag in {"embed", "iframe"}:
            value = attrs.get("src")
        elif tag == "a":
            value = attrs.get("href")
        else:
            return
        if value:
            self.links.append(value.strip())


def _clean_filename(value: str, final_url: str) -> str:
    filename = value or Path(urlsplit(final_url).path).name or "document.pdf"
    filename = filename.replace("\\", "/").split("/")[-1]
    filename = "".join(ch for ch in filename if ch >= " " and ch not in '<>:"|?*').strip().rstrip(".")
    if not filename:
        filename = "document.pdf"
    if not filename.lower().endswith(".pdf"):
        filename += ".pdf"
    return filename[:240]


def _pdf_filename(headers: dict[str, str], final_url: str) -> str:
    disposition = headers.get("content-disposition", "")
    match = re.search(r"filename\*\s*=\s*([^;]+)", disposition, re.I)
    filename = None
    if match:
        token = match.group(1).strip().strip('"')
        if "''" in token:
            charset, encoded = token.split("''", 1)
            try:
                filename = unquote_to_bytes(encoded).decode(charset or "utf-8", errors="replace")
            except LookupError:
                filename = unquote_to_bytes(encoded).decode("utf-8", errors="replace")
    if not filename:
        match = re.search(r"filename\s*=\s*(?:\"((?:\\.|[^\"])*)\"|([^;]+))", disposition, re.I)
        if match:
            filename = (match.group(1) or match.group(2) or "").replace('\\"', '"').strip()
    return _clean_filename(filename or "", final_url)


def _verify_pdf(body: bytes) -> None:
    if not body or b"%PDF-" not in body[:1024]:
        raise PdfFetchError("Le contenu reçu n'a pas d'en-tête %PDF-")


def _pdf_pages(data: bytes) -> int:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise PdfFetchError("pypdf est nécessaire pour compter les pages PDF") from exc
    try:
        return len(PdfReader(io.BytesIO(data), strict=False).pages)
    except Exception as exc:
        raise PdfFetchError("Le contenu %PDF- reçu n'est pas un PDF lisible") from exc


async def _fetch_pdf(request, url: str, max_bytes: int) -> dict:
    """Download/verify a PDF using the selected request transport.

    GET is limited to HTTPS curs.upb.ro/2026 resources and Moodle pluginfile.php
    links on that same host. Redirects are inspected and validated one hop at a
    time. Wrapper HTML is bounded separately; response bodies are checked against
    max_bytes even if Content-Length is absent or incorrect.
    """
    if not isinstance(max_bytes, int) or max_bytes <= 0:
        raise ValueError("max_bytes doit être un entier positif")
    _authorized_url(url)
    wrapper_limit = min(MAX_WRAPPER_BYTES, max_bytes)
    body, headers, final_url = await _get_bounded(
        request, url, timeout=60000, max_bytes=max_bytes
    )
    content_type = headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if b"%PDF-" not in body[:1024] and (content_type == "text/html" or body.lstrip().lower().startswith((b"<!doctype html", b"<html"))):
        if len(body) > wrapper_limit:
            raise PdfFetchError("La page enveloppe Moodle dépasse la limite de taille")
        try:
            html_text = body.decode("utf-8", errors="replace")
            parser = _MoodleFileLinkParser()
            parser.feed(html_text)
            parser.close()
        except Exception as exc:
            raise PdfFetchError("Impossible de lire l'enveloppe HTML de la ressource") from exc
        candidates = []
        for raw_link in parser.links:
            candidate = urljoin(final_url, raw_link)
            try:
                candidate = _authorized_url(candidate)
            except PdfFetchError:
                continue
            if re.search(r"/pluginfile\.php(?:/|$)", urlsplit(candidate).path):
                candidates.append(candidate)
        if not candidates:
            raise PdfFetchError("La page enveloppe ne contient pas de lien Moodle pluginfile.php sûr")
        last_error = None
        for candidate in candidates:
            try:
                body, headers, final_url = await _get_bounded(
                    request, candidate, timeout=60000, max_bytes=max_bytes
                )
                _verify_pdf(body)
                break
            except PdfFetchError as exc:
                last_error = exc
        else:
            raise PdfFetchError("Tous les liens pluginfile.php ont échoué") from last_error

    _verify_pdf(body)
    return {
        "bytes": body,
        "sha256": hashlib.sha256(body).hexdigest(),
        "final_url": final_url,
        "filename": _pdf_filename(headers, final_url),
        "pages": _pdf_pages(body),
    }


async def fetch_pdf(request, url: str, max_bytes: int = 25 * 1024 * 1024,
                    *, browser_context=None) -> dict:
    """Fetch through a bounded stream using the dedicated context's cookies.

    Real Playwright contexts expose storage_state() and are never downloaded
    through APIRequestContext.get(), whose responses are buffered in full. The
    documented buffering contract is at
    https://playwright.dev/python/docs/api/class-apirequestcontext#dispose.
    The optional browser_context also synchronizes authorized Set-Cookie changes
    back to that same dedicated context. Cookies stay in memory; browser profiles
    and personal cookie stores are never read. Objects implementing only get()
    retain the legacy buffered adapter for compatibility with request doubles.
    """
    if not isinstance(max_bytes, int) or max_bytes <= 0:
        raise ValueError("max_bytes doit être un entier positif")
    _authorized_url(url)
    streaming = None
    if browser_context is not None and callable(getattr(browser_context, "cookies", None)):
        streaming = _BoundedMoodleRequest(await browser_context.cookies())
    elif callable(getattr(request, "storage_state", None)):
        state = await request.storage_state()
        streaming = _BoundedMoodleRequest(state.get("cookies", []))
    try:
        return await _fetch_pdf(streaming if streaming is not None else request, url, max_bytes)
    finally:
        if streaming is not None and browser_context is not None:
            updates = streaming.cookie_updates()
            if updates:
                await browser_context.add_cookies(updates)


def _assert_no_symlinks(path: Path) -> None:
    # Do not lstat unrelated ancestors (some Windows profile parents are not
    # readable by the sandbox). The store root itself and every descendant used
    # below it are checked, then resolved paths are constrained to that root.
    if path.is_symlink():
        raise PdfArchiveError(f"Lien symbolique interdit: {path}")


def _safe_course_dir(store_dir: str | os.PathLike, course_id: str) -> tuple[Path, Path]:
    if not isinstance(course_id, str) or not COURSE_ID_RE.fullmatch(course_id):
        raise PdfArchiveError("Identifiant de cours invalide")
    root_input = Path(store_dir)
    _assert_no_symlinks(root_input)
    try:
        root = root_input.resolve(strict=True)
    except FileNotFoundError as exc:
        raise PdfArchiveError("Le dossier d'archive doit exister") from exc
    if not root.is_dir():
        raise PdfArchiveError("Le dossier d'archive n'est pas un dossier")
    course_input = root / course_id
    if course_input.is_symlink():
        raise PdfArchiveError("Le dossier de cours ne peut pas être un lien symbolique")
    try:
        course = course_input.resolve(strict=True)
        course.relative_to(root)
    except (FileNotFoundError, ValueError) as exc:
        raise PdfArchiveError("Le sous-dossier du cours doit être précréé dans le magasin") from exc
    if not course.is_dir():
        raise PdfArchiveError("Le sous-dossier du cours n'est pas un dossier")
    return root, course


def _ensure_child_dir(parent: Path, name: str, root: Path) -> Path:
    candidate = parent / name
    if candidate.is_symlink():
        raise PdfArchiveError(f"Lien symbolique interdit: {candidate}")
    candidate.mkdir(exist_ok=True)
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(root)
    except (OSError, ValueError) as exc:
        raise PdfArchiveError("Dossier d'archive hors du magasin") from exc
    if not resolved.is_dir():
        raise PdfArchiveError("Dossier d'archive attendu")
    return resolved


def _safe_catalog_key(item_key: str) -> str:
    if not isinstance(item_key, str) or not item_key.strip() or len(item_key) > 400:
        raise PdfArchiveError("Clé d'item invalide")
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", item_key).strip(".-_")[:72] or "item"
    suffix = hashlib.sha256(item_key.encode("utf-8")).hexdigest()[:12]
    return f"{slug}-{suffix}.json"


def _safe_source_url(url: str) -> str:
    try:
        _authorized_url(url, allow_pluginfile=False)
    except PdfFetchError as exc:
        raise PdfArchiveError("source_url doit être une URL HTTPS du cours 2026") from exc
    return url


def _atomic_json(path: Path, value: dict) -> None:
    fd, temp_path = tempfile.mkstemp(prefix=".moodle-pdf-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        if os.name == "posix":
            os.chmod(temp_path, 0o640)
        os.replace(temp_path, path)
    except BaseException:
        try:
            os.unlink(temp_path)
        except FileNotFoundError:
            pass
        raise


def _write_immutable(path: Path, data: bytes) -> None:
    if path.is_symlink():
        raise PdfArchiveError(f"Lien symbolique interdit: {path}")
    if path.exists():
        existing_sha = hashlib.sha256(path.read_bytes()).hexdigest()
        if existing_sha != path.stem:
            raise PdfArchiveError(f"Le fichier versionné {path.name} ne correspond pas à son SHA256")
        return
    fd, temp_path = tempfile.mkstemp(prefix=".pdf-version-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if os.name == "posix":
            os.chmod(temp_path, 0o640)
        try:
            os.link(temp_path, path)
        except FileExistsError:
            if hashlib.sha256(path.read_bytes()).hexdigest() != path.stem:
                raise PdfArchiveError(f"Conflit avec le fichier versionné {path.name}")
    finally:
        try:
            os.unlink(temp_path)
        except FileNotFoundError:
            pass


def _load_catalog(path: Path, course_id: str, item_key: str) -> dict | None:
    if path.is_symlink():
        raise PdfArchiveError(f"Lien symbolique interdit: {path}")
    if not path.exists():
        return None
    try:
        catalog = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PdfArchiveError("Catalogue existant illisible; historique non modifié") from exc
    if (not isinstance(catalog, dict) or set(catalog) != {"version", "course", "key", "title", "source_url", "current_sha", "versions"}
            or catalog.get("version") != 1
            or catalog.get("course") != course_id or catalog.get("key") != item_key
            or not isinstance(catalog.get("versions"), list)):
        raise PdfArchiveError("Catalogue existant incompatible; historique non modifié")
    for entry in catalog["versions"]:
        if (not isinstance(entry, dict)
                or set(entry) != {"sha256", "filename", "pages", "relative_file", "bytes", "downloaded_utc"}
                or not re.fullmatch(r"[0-9a-f]{64}", str(entry.get("sha256", "")))
                or entry.get("relative_file") != f"{course_id}/files/{entry.get('sha256')}.pdf"
                or type(entry.get("pages")) is not int or entry["pages"] < 0):
            raise PdfArchiveError("Catalogue existant contient une version invalide; historique non modifié")
    current_sha = catalog.get("current_sha")
    if current_sha and not any(v.get("sha256") == current_sha for v in catalog["versions"] if isinstance(v, dict)):
        raise PdfArchiveError("Catalogue incohérent: current_sha absent de versions")
    return catalog


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def archive_pdf(result: dict, store_dir: str | os.PathLike, course_id: str,
                item_key: str, title: str, source_url: str) -> dict:
    """Archive one downloaded version immutably and update its per-resource catalog.

    The course directory must already exist below store_dir. Version files are
    content-addressed by SHA256; old files are never replaced. The JSON catalog is
    atomically replaced after its referenced PDF has been written and verified.
    """
    if not isinstance(result, dict) or not isinstance(result.get("bytes"), bytes):
        raise PdfArchiveError("result doit contenir bytes")
    body = result["bytes"]
    _verify_pdf(body)
    digest = hashlib.sha256(body).hexdigest()
    if result.get("sha256") != digest:
        raise PdfArchiveError("SHA256 de result incompatible avec les octets")
    pages = result.get("pages")
    if type(pages) is not int or pages < 0:
        raise PdfArchiveError("result.pages doit être un entier positif ou nul")
    final_url = result.get("final_url")
    try:
        _authorized_url(final_url)
    except PdfFetchError as exc:
        raise PdfArchiveError("final_url doit rester sur Moodle ou pluginfile du même hôte") from exc
    filename = _clean_filename(str(result.get("filename") or ""), final_url)
    source_url = _safe_source_url(source_url)
    if not isinstance(title, str) or not title.strip():
        raise PdfArchiveError("title doit être non vide")
    catalog_filename = _safe_catalog_key(item_key)
    _, course_dir = _safe_course_dir(store_dir, course_id)
    files_dir = _ensure_child_dir(course_dir, "files", course_dir)
    catalog_dir = _ensure_child_dir(course_dir, "catalog", course_dir)
    catalog_path = catalog_dir / catalog_filename
    if catalog_path.resolve(strict=False).parent != catalog_dir:
        raise PdfArchiveError("Chemin de catalogue invalide")
    catalog = _load_catalog(catalog_path, course_id, item_key)
    old_sha = catalog.get("current_sha") if catalog else None
    old_version = next((v for v in reversed(catalog["versions"]) if v.get("sha256") == old_sha), None) if catalog else None
    old_pages = old_version.get("pages") if old_version else None
    changed = bool(old_sha and old_sha != digest)
    versions = list(catalog["versions"]) if catalog else []
    pdf_path = files_dir / f"{digest}.pdf"
    _write_immutable(pdf_path, body)
    if old_sha != digest:
        versions.append({
            "sha256": digest,
            "filename": filename,
            "pages": pages,
            "relative_file": f"{course_id}/files/{digest}.pdf",
            "bytes": len(body),
            "downloaded_utc": _utc_now(),
        })
    elif versions:
        # Keep the original version record intact; current title/source metadata can
        # change independently of PDF bytes.
        pass
    new_catalog = {
        "version": 1,
        "course": course_id,
        "key": item_key,
        "title": title,
        "source_url": source_url,
        "current_sha": digest,
        "versions": versions,
    }
    if catalog is None or new_catalog != catalog:
        _atomic_json(catalog_path, new_catalog)
    return {
        "changed": changed,
        "old_pages": old_pages,
        "new_pages": pages,
        "current_sha": digest,
        "relative_file": f"{course_id}/files/{digest}.pdf",
        "catalog_path": str(catalog_path),
        "catalog": new_catalog,
    }


def _read_pdf(value) -> bytes:
    return value if isinstance(value, bytes) else Path(value).read_bytes()


def extract_added_pages(previous_pdf, current_pdf) -> list[dict]:
    """Extract text from appended pages in current_pdf beyond previous_pdf's page count.

    The helper is intended for instructor PDFs updated by appending slides. It does
    not attempt to infer pages inserted or reordered in the middle of a document.
    """
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise RuntimeError("pypdf est nécessaire pour extraire le texte des nouvelles pages") from exc
    try:
        previous_reader = PdfReader(io.BytesIO(_read_pdf(previous_pdf)), strict=False)
        current_reader = PdfReader(io.BytesIO(_read_pdf(current_pdf)), strict=False)
        old_count = len(previous_reader.pages)
        return [
            {"page_number": index + 1, "text": current_reader.pages[index].extract_text() or ""}
            for index in range(old_count, len(current_reader.pages))
        ]
    except Exception as exc:
        raise RuntimeError("Impossible de lire ou d'extraire le PDF") from exc
