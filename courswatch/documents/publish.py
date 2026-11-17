"""Publish immutable Moodle PDF versions and page text into a course vault."""

from __future__ import annotations

import hashlib
import html
import json
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit


COURSE_IDS = frozenset({"AAIT", "AITDM", "BDFA", "SOS"})
CATALOG_KEYS = frozenset({
    "version", "course", "key", "title", "source_url", "current_sha", "versions",
})
VERSION_KEYS = frozenset({
    "sha256", "filename", "pages", "relative_file", "bytes", "downloaded_utc",
})
MANAGED_MARKER = "managed_by: cours-watch"
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
COURSE_KEY_RE = re.compile(r"^[A-Z0-9_-]{2,16}$")


class PublishError(RuntimeError):
    """Raised when an archive or vault path is unsafe or inconsistent."""


@dataclass(frozen=True)
class CatalogVersion:
    course: str
    key: str
    title: str
    source_url: str
    current_sha: str
    sha256: str
    filename: str
    pages: int
    relative_file: str
    byte_count: int
    downloaded_utc: str
    source_path: Path


@dataclass
class PdfVersion:
    course: str
    sha256: str
    source_path: Path
    byte_count: int
    pages: int
    page_texts: list[str]
    references: list[CatalogVersion]


def _is_linklike(path: Path) -> bool:
    junction_check = getattr(path, "is_junction", None)
    return path.is_symlink() or bool(junction_check and junction_check())


def _assert_no_links(path: Path) -> None:
    absolute = Path(os.path.abspath(path))
    components = list(reversed((absolute, *absolute.parents)))
    for component in components:
        if _is_linklike(component):
            raise PublishError(f"Lien symbolique/jonction refusé: {component}")


def _existing_root(value: str | os.PathLike[str], label: str) -> Path:
    input_path = Path(value).expanduser()
    _assert_no_links(input_path)
    try:
        root = input_path.resolve(strict=True)
    except OSError as exc:
        raise PublishError(f"Racine {label} absente ou inaccessible") from exc
    if not root.is_dir():
        raise PublishError(f"Racine {label} n’est pas un dossier")
    return root


def _check_contained(path: Path, root: Path) -> None:
    _assert_no_links(path)
    try:
        path.resolve(strict=False).relative_to(root)
    except (OSError, ValueError) as exc:
        raise PublishError(f"Chemin hors racine autorisée: {path.name}") from exc


def _validate_source_url(value: object) -> str:
    if not isinstance(value, str) or len(value) > 4096:
        raise PublishError("source_url invalide")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise PublishError("source_url invalide") from exc
    if (parsed.scheme.lower() != "https" or parsed.hostname != "curs.upb.ro"
            or parsed.username is not None or parsed.password is not None
            or port not in (None, 443) or not parsed.path.startswith("/2026/")):
        raise PublishError("source_url doit être une URL HTTPS du Moodle 2026")
    return value


def _safe_catalog_filename(key: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", key).strip(".-_")[:72] or "item"
    suffix = hashlib.sha256(key.encode("utf-8")).hexdigest()[:12]
    return f"{slug}-{suffix}.json"


def _safe_store_relative(value: object, course: str, digest: str) -> PurePosixPath:
    expected = f"{course}/files/{digest}.pdf"
    if not isinstance(value, str) or value != expected:
        raise PublishError("relative_file incompatible avec le chemin immuable attendu")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or "\\" in value:
        raise PublishError("relative_file non sûr")
    return path


def _parse_time(value: object) -> None:
    if not isinstance(value, str) or not value:
        raise PublishError("downloaded_utc manquant")
    try:
        from datetime import datetime, timezone
        normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
        parsed = datetime.fromisoformat(normalized)
        if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
            raise ValueError("downloaded_utc doit être en UTC")
    except ValueError as exc:
        raise PublishError("downloaded_utc invalide") from exc


def _load_catalog(catalog_path: Path, store_root: Path, course: str) -> list[CatalogVersion]:
    _check_contained(catalog_path, store_root)
    if _is_linklike(catalog_path) or not catalog_path.is_file():
        raise PublishError("Fichier de catalogue manquant ou non régulier")
    try:
        data = json.loads(catalog_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PublishError(f"Catalogue illisible: {catalog_path.name}") from exc
    if (not isinstance(data, dict) or set(data) != CATALOG_KEYS
            or type(data.get("version")) is not int or data.get("version") != 1
            or data.get("course") != course
            or not isinstance(data.get("key"), str) or not data["key"].strip()
            or len(data["key"]) > 400
            or not isinstance(data.get("title"), str) or not data["title"].strip()
            or not isinstance(data.get("versions"), list)):
        raise PublishError(f"Schéma de catalogue invalide: {catalog_path.name}")
    key = data["key"]
    if catalog_path.name != _safe_catalog_filename(key):
        raise PublishError(f"Nom de catalogue non conforme à sa clé: {catalog_path.name}")
    source_url = _validate_source_url(data.get("source_url"))
    current_sha = data.get("current_sha")
    if not isinstance(current_sha, str) or not SHA_RE.fullmatch(current_sha):
        raise PublishError("current_sha invalide")

    seen: set[str] = set()
    versions: list[CatalogVersion] = []
    for entry in data["versions"]:
        if not isinstance(entry, dict) or set(entry) != VERSION_KEYS:
            raise PublishError("Entrée de version au schéma invalide")
        digest = entry.get("sha256")
        if not isinstance(digest, str) or not SHA_RE.fullmatch(digest) or digest in seen:
            raise PublishError("SHA de version invalide ou dupliqué")
        seen.add(digest)
        filename = entry.get("filename")
        if (not isinstance(filename, str) or not filename.strip()
                or Path(filename).name != filename or "/" in filename or "\\" in filename):
            raise PublishError("Nom PDF invalide dans le catalogue")
        pages = entry.get("pages")
        byte_count = entry.get("bytes")
        if type(pages) is not int or pages < 0 or type(byte_count) is not int or byte_count <= 0:
            raise PublishError("pages ou bytes invalide")
        relative = _safe_store_relative(entry.get("relative_file"), course, digest)
        downloaded_utc = entry.get("downloaded_utc")
        _parse_time(downloaded_utc)
        source_path = store_root.joinpath(*relative.parts)
        _check_contained(source_path, store_root)
        if _is_linklike(source_path) or not source_path.is_file():
            raise PublishError(f"PDF versionné absent ou lien interdit: {relative.as_posix()}")
        actual_size, actual_sha = _hash_file(source_path)
        if actual_size != byte_count or actual_sha != digest:
            raise PublishError(f"Hash/taille du PDF source incompatible: {relative.as_posix()}")
        with source_path.open("rb") as stream:
            if b"%PDF-" not in stream.read(1024):
                raise PublishError(f"Fichier sans signature PDF: {relative.as_posix()}")
        versions.append(CatalogVersion(
            course=course, key=key, title=data["title"].strip(), source_url=source_url,
            current_sha=current_sha, sha256=digest, filename=filename, pages=pages,
            relative_file=relative.as_posix(), byte_count=byte_count,
            downloaded_utc=downloaded_utc, source_path=source_path,
        ))
    if not versions or current_sha not in seen:
        raise PublishError("Catalogue sans version courante correspondante")
    if versions[-1].sha256 != current_sha:
        raise PublishError("La version courante doit être le dernier élément de l’historique")
    return versions


def _hash_file(path: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
    return size, digest.hexdigest()


def _collect_catalogs(store_root: Path) -> dict[tuple[str, str], list[CatalogVersion]]:
    grouped: dict[tuple[str, str], list[CatalogVersion]] = {}
    seen_keys: set[tuple[str, str]] = set()
    for child in sorted(store_root.iterdir(), key=lambda p: p.name.casefold()):
        if _is_linklike(child):
            raise PublishError(f"Lien symbolique/jonction interdit dans le store: {child.name}")
        if not child.is_dir():
            continue
        course = child.name
        if course not in COURSE_IDS or not COURSE_KEY_RE.fullmatch(course):
            raise PublishError(f"Identifiant de cours non prévu: {course}")
        catalog_dir = child / "catalog"
        files_dir = child / "files"
        for directory in (catalog_dir, files_dir):
            if _is_linklike(directory):
                raise PublishError(f"Lien symbolique/jonction interdit: {directory.name}")
        if not catalog_dir.exists() and not files_dir.exists():
            continue
        if not catalog_dir.is_dir() or not files_dir.is_dir():
            raise PublishError(f"Répertoires catalog/files incomplets pour {course}")
        _check_contained(catalog_dir, store_root)
        _check_contained(files_dir, store_root)
        for entry in sorted(catalog_dir.iterdir(), key=lambda p: p.name.casefold()):
            if _is_linklike(entry):
                raise PublishError(f"Lien symbolique/jonction interdit dans le catalogue: {entry.name}")
            if entry.suffix.casefold() != ".json":
                continue
            versions = _load_catalog(entry, store_root, course)
            first = versions[0]
            identity = (course, first.key)
            if identity in seen_keys:
                raise PublishError(f"Clé de ressource dupliquée dans {course}")
            seen_keys.add(identity)
            grouped[identity] = versions
    return grouped


def _extract_pages(path: Path, expected_pages: int) -> list[str]:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise PublishError("pypdf est requis pour extraire le texte des PDF") from exc
    try:
        with path.open("rb") as stream:
            reader = PdfReader(stream, strict=False)
            actual_pages = len(reader.pages)
            if actual_pages != expected_pages:
                raise PublishError(
                    f"Nombre de pages différent du catalogue pour {path.name}: "
                    f"{actual_pages} != {expected_pages}"
                )
            extracted: list[str] = []
            for page in reader.pages:
                text = page.extract_text() or ""
                text = text.replace("\x00", "").replace("\r\n", "\n").replace("\r", "\n").strip()
                extracted.append(text)
            return extracted
    except PublishError:
        raise
    except Exception as exc:
        raise PublishError(f"PDF illisible ou extraction impossible: {path.name}") from exc


def _ensure_directory(root: Path, relative: PurePosixPath) -> Path:
    if relative.is_absolute() or ".." in relative.parts:
        raise PublishError("Chemin de dossier relatif non sûr")
    current = root
    for part in relative.parts:
        current = current / part
        if _is_linklike(current):
            raise PublishError(f"Lien symbolique/jonction interdit: {current.name}")
        if not current.exists():
            try:
                # Inherit the master's SGID bit/group. Explicit setgid creation
                # is correctly forbidden by systemd RestrictSUIDSGID.
                current.mkdir(mode=0o770)
            except FileExistsError:
                pass
            if not current.is_dir() or _is_linklike(current):
                raise PublishError(f"Répertoire de publication invalide: {current.name}")
        _check_contained(current, root)
        if not current.is_dir():
            raise PublishError(f"Répertoire attendu: {current.name}")
    return current


def _atomic_create_bytes(path: Path, data: bytes, root: Path, mode: int = 0o660) -> bool:
    """Create a new immutable file atomically; never replace an existing path."""
    _check_contained(path, root)
    if _is_linklike(path):
        raise PublishError(f"Lien symbolique/jonction interdit: {path.name}")
    if path.exists():
        if not path.is_file():
            raise PublishError(f"Cible existante non régulière: {path.name}")
        existing_size, existing_sha = _hash_file(path)
        if existing_size != len(data) or existing_sha != hashlib.sha256(data).hexdigest():
            raise PublishError(f"Conflit: le fichier existant diffère, conservé: {path.name}")
        os.chmod(path, mode)
        return False
    fd, temp_name = tempfile.mkstemp(prefix=".cours-watch-", suffix=".tmp", dir=path.parent)
    temp_path = Path(temp_name)
    try:
        os.chmod(temp_path, mode)
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temp_path, path)
            return True
        except FileExistsError:
            existing_size, existing_sha = _hash_file(path)
            if existing_size != len(data) or existing_sha != hashlib.sha256(data).hexdigest():
                raise PublishError(f"Conflit concurrent, fichier préservé: {path.name}")
            os.chmod(path, mode)
            return False
    finally:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass


def _has_managed_marker(data: bytes) -> bool:
    try:
        text = data.decode("utf-8-sig")
    except UnicodeError:
        return False
    return any(line.strip() == MANAGED_MARKER for line in text.splitlines()[:20])


def _atomic_replace_managed(path: Path, data: bytes, root: Path) -> bool:
    """Create a managed Markdown file or atomically refresh an already managed index."""
    _check_contained(path, root)
    if _is_linklike(path):
        raise PublishError(f"Lien symbolique/jonction interdit: {path.name}")
    if path.exists():
        if not path.is_file():
            raise PublishError(f"Fichier Markdown non régulier: {path.name}")
        existing = path.read_bytes()
        if not _has_managed_marker(existing):
            raise PublishError(f"Page existante sans marqueur {MANAGED_MARKER}: {path.name}")
        if existing == data:
            os.chmod(path, 0o660)
            return False
    else:
        return _atomic_create_bytes(path, data, root)

    fd, temp_name = tempfile.mkstemp(prefix=".cours-watch-", suffix=".tmp", dir=path.parent)
    temp_path = Path(temp_name)
    try:
        os.chmod(temp_path, 0o660)
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        # Recheck ownership immediately before the atomic replace.
        if _is_linklike(path) or not path.is_file():
            raise PublishError(f"Cible modifiée pendant publication: {path.name}")
        current = path.read_bytes()
        if not _has_managed_marker(current):
            raise PublishError(f"Page devenue manuelle pendant publication: {path.name}")
        os.replace(temp_path, path)
        return True
    finally:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass


def _markdown_escape(value: str) -> str:
    value = re.sub(r"\s+", " ", value).strip()
    return html.escape(value, quote=False).replace("|", "\\|").replace("[", "\\[").replace("]", "\\]")


def _code_escape(value: str) -> str:
    return value.replace("`", "\\`").replace("|", "\\|").replace("\n", " ")


def _render_version_note(version: PdfVersion) -> bytes:
    course_for_link = version.course
    title = version.references[0].title
    lines = [
        "---",
        "managed_by: cours-watch",
        f"course: {version.course}",
        f"pdf_sha256: {version.sha256}",
        f"pages: {version.pages}",
        "---",
        "",
        f"# {_markdown_escape(title)} — texte intégral",
        "",
        f"- SHA256 : `{version.sha256}`",
        f"- Taille : {version.byte_count} octets",
        f"- Pages : {version.pages}",
        f"- PDF conservé : [version immuable](../../../Documents%20-%20versions/{version.course}/{version.sha256}.pdf)",
        "",
        "## Ressources Moodle associées",
        "",
    ]
    for reference in sorted(version.references, key=lambda item: (item.title.casefold(), item.key)):
        lines.append(f"- **{_markdown_escape(reference.title)}** (`{reference.key}`) — <{reference.source_url}>")
    lines += ["", "## Texte par page", ""]
    for index, text in enumerate(version.page_texts, start=1):
        lines.append(f"### Page {index}")
        lines.append("")
        lines.append(f"[Ouvrir le PDF à la page {index}](../../../Documents%20-%20versions/{version.course}/{version.sha256}.pdf#page={index})")
        lines.append("")
        lines.append(text if text else "*(Aucun texte extractible sur cette page.)*")
        lines.append("")
    return ("\n".join(lines).rstrip() + "\n").encode("utf-8")


def _render_course_index(course: str, resources: list[list[CatalogVersion]]) -> bytes:
    lines = [
        "---",
        "managed_by: cours-watch",
        f"course: {course}",
        "type: pdf-version-index",
        "---",
        "",
        f"# {course} — Versions des PDF Moodle",
        "",
        "Cet index est généré depuis le store immuable de versions Moodle.",
        "",
    ]
    for versions in sorted(resources, key=lambda group: (group[0].title.casefold(), group[0].key)):
        current_sha = versions[0].current_sha
        current = next(version for version in versions if version.sha256 == current_sha)
        lines += [
            f"## {_markdown_escape(current.title)}",
            "",
            f"- Clé Moodle : `{_code_escape(current.key)}`",
            f"- Ressource : <{current.source_url}>",
            f"- Version courante : `{current_sha}` ({current.pages} pages, {current.byte_count} octets)",
            "",
            "| Version | Date de collecte | Pages | Variation | PDF | Texte intégral |",
            "| --- | --- | ---: | ---: | --- | --- |",
        ]
        previous_pages: int | None = None
        for version in versions:
            if previous_pages is None:
                delta = "—"
            else:
                difference = version.pages - previous_pages
                delta = f"{difference:+d}" if difference else "0"
            pdf = f"[PDF](../../Documents%20-%20versions/{course}/{version.sha256}.pdf)"
            text = f"[Pages](Documents/{version.sha256}.md)"
            current_mark = " **courante**" if version.sha256 == current_sha else ""
            lines.append(
                f"| `{version.sha256[:12]}`{current_mark} | {version.downloaded_utc} | "
                f"{version.pages} | {delta} | {pdf} | {text} |"
            )
            previous_pages = version.pages
        lines += ["", f"[Ressource Moodle]({current.source_url})", ""]
    return ("\n".join(lines).rstrip() + "\n").encode("utf-8")


def _validate_and_extract(grouped: dict[tuple[str, str], list[CatalogVersion]]) -> dict[tuple[str, str], PdfVersion]:
    by_sha: dict[tuple[str, str], list[CatalogVersion]] = {}
    for (course, _key), versions in grouped.items():
        for version in versions:
            by_sha.setdefault((course, version.sha256), []).append(version)
    result: dict[tuple[str, str], PdfVersion] = {}
    for identity, references in by_sha.items():
        course, digest = identity
        first = references[0]
        if any(reference.pages != first.pages or reference.byte_count != first.byte_count for reference in references):
            raise PublishError(f"Métadonnées incompatibles pour le PDF partagé {digest[:12]}")
        page_texts = _extract_pages(first.source_path, first.pages)
        result[identity] = PdfVersion(
            course=course, sha256=digest, source_path=first.source_path,
            byte_count=first.byte_count, pages=first.pages, page_texts=page_texts,
            references=references,
        )
    return result


def _copy_pdf_immutable(version: PdfVersion, vault_root: Path) -> bool:
    destination_dir = _ensure_directory(vault_root, PurePosixPath("Documents - versions") / version.course)
    destination = destination_dir / f"{version.sha256}.pdf"
    source_size, source_sha = _hash_file(version.source_path)
    if source_size != version.byte_count or source_sha != version.sha256:
        raise PublishError(f"PDF source modifié depuis validation: {version.sha256[:12]}")
    data = version.source_path.read_bytes()
    if hashlib.sha256(data).hexdigest() != version.sha256:
        raise PublishError(f"PDF source modifié pendant lecture: {version.sha256[:12]}")
    return _atomic_create_bytes(destination, data, vault_root)


def publish_pdf_versions(store_dir: str | os.PathLike[str], vault_dir: str | os.PathLike[str]) -> dict:
    """Publish all validated Moodle PDF versions and extracted page text.

    ``relative_file`` in catalog entries is relative to ``store_dir`` and must
    equal ``<course_id>/files/<sha256>.pdf``. Existing immutable PDFs and
    PDFs are never overwritten; managed per-SHA notes and the course index may
    be atomically refreshed when source metadata changes. No files are deleted.
    """
    store_root = _existing_root(store_dir, "store")
    vault_root = _existing_root(vault_dir, "vault")
    grouped = _collect_catalogs(store_root)
    versions_by_sha = _validate_and_extract(grouped)

    # Preflight every managed Markdown destination before publishing any bytes.
    notes_root = vault_root / "Notes"
    if _is_linklike(notes_root):
        raise PublishError("Notes ne peut pas être un lien symbolique/jonction")
    for course, digest in versions_by_sha:
        version = versions_by_sha[(course, digest)]
        note_path = notes_root / course / "Documents" / f"{digest}.md"
        _preflight_managed_target(note_path)
        pdf_path = vault_root / "Documents - versions" / course / f"{digest}.pdf"
        _check_contained(pdf_path, vault_root)
        if _is_linklike(pdf_path):
            raise PublishError(f"Lien symbolique/jonction interdit: {pdf_path.name}")
        if pdf_path.exists():
            if not pdf_path.is_file():
                raise PublishError(f"Cible PDF non régulière: {pdf_path.name}")
            pdf_size, pdf_sha = _hash_file(pdf_path)
            if pdf_size != version.byte_count or pdf_sha != digest:
                raise PublishError(f"Conflit PDF préexistant, préservé: {pdf_path.name}")
    for course in sorted({course for course, _key in grouped}):
        _preflight_managed_target(notes_root / course / f"{course} - Versions des PDF.md")

    copied_pdfs = 0
    created_text_notes = 0
    updated_text_notes = 0
    for identity in sorted(versions_by_sha):
        version = versions_by_sha[identity]
        if _copy_pdf_immutable(version, vault_root):
            copied_pdfs += 1
        note_dir = _ensure_directory(vault_root, PurePosixPath("Notes", version.course, "Documents"))
        note_path = note_dir / f"{version.sha256}.md"
        note_action = _publish_version_note(note_path, _render_version_note(version), vault_root)
        if note_action == "created":
            created_text_notes += 1
        elif note_action == "updated":
            updated_text_notes += 1

    updated_indexes = 0
    by_course: dict[str, list[list[CatalogVersion]]] = {}
    for (course, _key), versions in grouped.items():
        by_course.setdefault(course, []).append(versions)
    for course, resource_groups in sorted(by_course.items()):
        course_note_dir = _ensure_directory(vault_root, PurePosixPath("Notes", course))
        index_path = course_note_dir / f"{course} - Versions des PDF.md"
        if _atomic_replace_managed(index_path, _render_course_index(course, resource_groups), vault_root):
            updated_indexes += 1

    return {
        "courses": sorted(by_course),
        "resources": len(grouped),
        "versions": len(versions_by_sha),
        "pdfs_copied": copied_pdfs,
        "text_notes_created": created_text_notes,
        "text_notes_updated": updated_text_notes,
        "indexes_updated": updated_indexes,
    }


def _preflight_managed_target(path: Path) -> None:
    _assert_no_links(path)
    if not path.exists():
        return
    if not path.is_file():
        raise PublishError(f"Cible Markdown non régulière: {path.name}")
    existing = path.read_bytes()
    if not _has_managed_marker(existing):
        raise PublishError(f"Page existante sans marqueur {MANAGED_MARKER}: {path.name}")


def _publish_version_note(path: Path, data: bytes, vault_root: Path) -> str | None:
    _check_contained(path, vault_root)
    if _is_linklike(path):
        raise PublishError(f"Lien symbolique/jonction interdit: {path.name}")
    if path.exists():
        if not path.is_file():
            raise PublishError(f"Note de version non régulière: {path.name}")
        old_data = path.read_bytes()
        if not _has_managed_marker(old_data):
            raise PublishError(f"Page existante sans marqueur {MANAGED_MARKER}: {path.name}")
        if old_data == data:
            os.chmod(path, 0o660)
            return None
        _atomic_replace_managed(path, data, vault_root)
        return "updated"
    return "created" if _atomic_create_bytes(path, data, vault_root) else None


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("store_dir", help="Racine du store de versions Moodle")
    parser.add_argument("vault_dir", help="Racine du coffre maître")
    args = parser.parse_args(argv)
    try:
        result = publish_pdf_versions(args.store_dir, args.vault_dir)
    except (PublishError, OSError) as exc:
        parser.exit(2, f"Erreur publication PDF: {exc}\n")
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
