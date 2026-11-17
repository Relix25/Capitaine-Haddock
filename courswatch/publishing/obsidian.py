"""Render a safe, managed Obsidian view from a cours-watch snapshot (stdlib only)."""

from __future__ import annotations

import argparse
import json
import os
import re
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote, urlsplit


MANAGED_BY = "cours-watch"
COURSE_IDS = {"AAIT", "AITDM", "BDFA", "SOS"}
SUMMARY_LINKS = {
    "AAIT": "[[AAIT - Accueil]]",
    "AITDM": "[[00 - AITDM]]",
    "BDFA": "[[BDFA - Accueil]]",
    "SOS": "[[SOS - Accueil]]",
}
TOP_KEYS = {"version", "checked_utc", "courses"}
COURSE_KEYS = {"label", "sources", "items", "fresh_items"}
SOURCE_KEYS = {"source", "status", "checked_utc", "observed_utc", "detail"}
ITEM_KEYS = {"source", "key", "title", "body", "url", "fingerprint"}


class SnapshotError(ValueError):
    """Raised when input is malformed or contains fields outside the public schema."""


class UnsafeVaultError(ValueError):
    """Raised when an output path could escape the vault or pass through a symlink."""


class ManualPageError(FileExistsError):
    """Raised rather than overwrite a page not owned by cours-watch."""


def _expect_object(value, name: str, allowed: set[str]) -> dict:
    if not isinstance(value, dict):
        raise SnapshotError(f"{name} doit être un objet JSON")
    unexpected = set(value) - allowed
    if unexpected:
        raise SnapshotError(f"Champs non autorisés dans {name}: {', '.join(sorted(unexpected))}")
    return value


def _require_text(value, name: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not allow_empty and not value.strip()):
        raise SnapshotError(f"{name} doit être une chaîne" + ("" if allow_empty else " non vide"))
    return value


def _parse_datetime(value, name: str) -> datetime:
    text = _require_text(value, name)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SnapshotError(f"{name} doit être une date ISO-8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise SnapshotError(f"{name} doit inclure un fuseau")
    return parsed.astimezone(timezone.utc)


def validate_snapshot(snapshot: object) -> dict:
    """Validate the public snapshot schema and return a normalized shallow copy."""
    data = _expect_object(snapshot, "snapshot", TOP_KEYS)
    if data.get("version") != 1:
        raise SnapshotError("version doit valoir 1")
    checked = _parse_datetime(data.get("checked_utc"), "checked_utc")
    courses = data.get("courses")
    if not isinstance(courses, dict) or not courses:
        raise SnapshotError("courses doit être un objet non vide")
    if set(courses) - COURSE_IDS:
        raise SnapshotError("ID de cours inconnu: " + ", ".join(sorted(set(courses) - COURSE_IDS)))

    normalized = {"version": 1, "checked_utc": checked.isoformat(), "courses": {}}
    for course_id, raw_course in courses.items():
        course = _expect_object(raw_course, f"courses.{course_id}", COURSE_KEYS)
        label = _require_text(course.get("label"), f"courses.{course_id}.label")
        sources = course.get("sources")
        items = course.get("items")
        if not isinstance(sources, list) or not sources or not isinstance(items, list):
            raise SnapshotError(f"courses.{course_id} requiert une liste sources non vide et une liste items")

        clean_sources = []
        source_ids = set()
        for index, raw_source in enumerate(sources):
            source = _expect_object(raw_source, f"courses.{course_id}.sources[{index}]", SOURCE_KEYS)
            source_id = _require_text(source.get("source"), "source")
            status = _require_text(source.get("status"), "status")
            if source_id in source_ids:
                raise SnapshotError(f"Source dupliquée: {source_id}")
            source_ids.add(source_id)
            clean_source = {"source": source_id, "status": status}
            for field in ("checked_utc", "observed_utc"):
                if field in source and source[field] is not None:
                    clean_source[field] = _parse_datetime(source[field], field).isoformat()
            if "detail" in source and source["detail"] is not None:
                clean_source["detail"] = _require_text(source["detail"], "detail", allow_empty=True)
            clean_sources.append(clean_source)

        clean_items = []
        item_keys = set()
        for index, raw_item in enumerate(items):
            item = _validate_item(raw_item, f"courses.{course_id}.items[{index}]")
            identity = (item["source"], item["key"])
            if identity in item_keys:
                raise SnapshotError(f"Item dupliqué: {identity[0]} / {identity[1]}")
            if item["source"] not in source_ids:
                raise SnapshotError(f"Source d'item absente de sources: {item['source']}")
            item_keys.add(identity)
            clean_items.append(item)

        clean_course = {"label": label, "sources": clean_sources, "items": clean_items}
        if "fresh_items" in course:
            fresh = course["fresh_items"]
            if not isinstance(fresh, list):
                raise SnapshotError("fresh_items doit être une liste")
            fresh_keys = set()
            known_by_key = {(item["source"], item["key"]): item for item in clean_items}
            for index, raw_item in enumerate(fresh):
                item = _validate_item(raw_item, f"courses.{course_id}.fresh_items[{index}]")
                identity = (item["source"], item["key"])
                if identity not in item_keys:
                    raise SnapshotError(f"fresh_items ne correspond pas à items: {identity}")
                if item != known_by_key[identity]:
                    raise SnapshotError(f"fresh_items contient une version différente de items: {identity}")
                if identity in fresh_keys:
                    raise SnapshotError(f"fresh_item dupliqué: {identity}")
                fresh_keys.add(identity)
            clean_course["fresh_items"] = [list(key) for key in sorted(fresh_keys)]
        normalized["courses"][course_id] = clean_course
    return normalized


def _validate_item(value: object, name: str) -> dict:
    item = _expect_object(value, name, ITEM_KEYS)
    clean = {
        "source": _require_text(item.get("source"), f"{name}.source"),
        "key": _require_text(item.get("key"), f"{name}.key"),
        "title": _require_text(item.get("title"), f"{name}.title", allow_empty=True),
        "body": _require_text(item.get("body"), f"{name}.body", allow_empty=True),
        "url": _require_text(item.get("url"), f"{name}.url"),
    }
    parsed_url = urlsplit(clean["url"])
    if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc or any(ch.isspace() for ch in clean["url"]):
        raise SnapshotError(f"{name}.url doit être une URL HTTP(S)")
    if "fingerprint" in item and item["fingerprint"] is not None:
        clean["fingerprint"] = _require_text(item["fingerprint"], f"{name}.fingerprint")
    return clean


def _last_sunday(year: int, month: int) -> datetime:
    from calendar import monthrange

    last_day = monthrange(year, month)[1]
    day = datetime(year, month, last_day).weekday()
    return datetime(year, month, last_day - ((day + 1) % 7), 1, tzinfo=timezone.utc)


def _bucharest_time(value: str) -> str:
    instant = _parse_datetime(value, "checked_utc")
    try:
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
    except ImportError:
        ZoneInfo = None
        ZoneInfoNotFoundError = KeyError
    try:
        local = instant.astimezone(ZoneInfo("Europe/Bucharest")) if ZoneInfo else None
    except (ZoneInfoNotFoundError, OSError):
        local = None
    if local is None:
        # Windows Python installations may not ship an IANA database. Romanian DST
        # follows the EU transition: 01:00 UTC on the last Sunday of March/October.
        start, end = _last_sunday(instant.year, 3), _last_sunday(instant.year, 10)
        local = instant.astimezone(timezone(timedelta(hours=3 if start <= instant < end else 2)))
    return local.strftime("%Y-%m-%d %H:%M:%S %z Europe/Bucharest")


def _clean_title(title: str) -> str:
    return " ".join(title.split()) or "Publication sans titre"


def _safe_url(url: str) -> str:
    return quote(url, safe=":/?#@!$&'*,;=%-._~")


def _frontmatter_managed(text: str) -> bool:
    if not text.startswith("---\n"):
        return False
    end = text.find("\n---\n", 4)
    return end >= 0 and re.search(r"(?m)^managed_by:\s*['\"]?cours-watch['\"]?\s*$", text[4:end]) is not None


def _safe_path(vault: Path, relative: Path, *, create_parent: bool = False) -> Path:
    vault = Path(vault)
    absolute_vault = vault.absolute()
    for component in reversed((absolute_vault, *absolute_vault.parents)):
        if component.is_symlink():
            raise UnsafeVaultError(f"Lien symbolique interdit dans le chemin du coffre: {component}")
    root = vault.resolve(strict=True)
    if not root.is_dir():
        raise UnsafeVaultError("Le coffre doit être un dossier existant")
    if relative.is_absolute() or ".." in relative.parts:
        raise UnsafeVaultError("Chemin de sortie hors du coffre")
    current = root
    parts = relative.parts
    for part in parts[:-1]:
        current = current / part
        if current.exists() or current.is_symlink():
            if current.is_symlink():
                raise UnsafeVaultError(f"Lien symbolique interdit: {current}")
            if not current.is_dir():
                raise UnsafeVaultError(f"Un dossier attendu est un fichier: {current}")
        elif create_parent:
            current.mkdir()
    target = current / parts[-1]
    if target.is_symlink():
        raise UnsafeVaultError(f"Lien symbolique interdit: {target}")
    try:
        target.resolve(strict=False).relative_to(root)
    except ValueError as exc:
        raise UnsafeVaultError("Chemin de sortie hors du coffre") from exc
    return target


def _atomic_write(path: Path, content: str) -> None:
    fd, temp_name = tempfile.mkstemp(prefix=".cours-watch-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temp_name,0o660)
        os.replace(temp_name, path)
    except BaseException:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise


def _source_status_map(course: dict) -> dict[str, dict]:
    return {entry["source"]: entry for entry in course["sources"]}


def _render_course(course_id: str, course: dict, checked_utc: str) -> tuple[str, dict]:
    fresh_keys = set(tuple(key) for key in course.get("fresh_items", [])) if "fresh_items" in course else None
    sources = _source_status_map(course)
    items_by_source = {source: [] for source in sources}
    for item in course["items"]:
        items_by_source[item["source"]].append(item)

    fresh_count = 0
    retained_count = 0
    error_count = sum(source["status"].lower() != "ok" for source in course["sources"])
    chunks = [
        "---",
        f"managed_by: {MANAGED_BY}",
        f"course: {course_id}",
        f"checked_utc: '{checked_utc}'",
        "---",
        f"# {course['label']} — veille automatique",
        "",
        f"Contrôle : **{_bucharest_time(checked_utc)}**.",
        f"Synthèse révisée du cours : {SUMMARY_LINKS[course_id]}.",
        f"PDF et texte intégral par page : [[{course_id} - Versions des PDF]].",
        "",
        "> Page gérée automatiquement par `cours-watch`. Elle documente le dernier contrôle; la synthèse révisée du cours reste le guide de travail.",
        "> Couverture non exhaustive : une interface virtualisée peut conserver des items anciens sans `last_seen` individuel. Un item retenu ne prouve pas qu'il était encore affiché à cette collecte.",
        "> Teams : publications/réponses effectivement chargées dans **Général** uniquement; fichiers non annoncés, autres canaux et Classwork hors couverture. Moodle : activités, dates, forums/fichiers dans les limites indiquées par le collecteur.",
        "",
        "## État des sources",
        "",
    ]
    summary = {"fresh": 0, "retained": 0, "errors": error_count,
               "freshness_known": fresh_keys is not None}
    for source_id, source in sources.items():
        failed = source["status"].lower() != "ok"
        current_items = items_by_source[source_id]
        if failed:
            state = f"**ERREUR : `{source['status']}`**"
        elif fresh_keys is None:
            state = "Collecte réussie; le snapshot ne distingue pas les items fraîchement vus des items retenus."
        else:
            fresh_here = [item for item in current_items if (source_id, item["key"]) in fresh_keys]
            if not fresh_here:
                state = "Collecte réussie; aucun changement signalé dans cette source pour ce contrôle."
            else:
                state = f"Collecte réussie; {len(fresh_here)} item(s) frais observé(s). La fraîcheur n'implique pas que le contenu ait changé."
        chunks.append(f"### {source_id}")
        chunks.append("")
        chunks.append(f"- État : {state}")
        if source.get("checked_utc"):
            chunks.append(f"- Contrôle source : {_bucharest_time(source['checked_utc'])}.")
        if source.get("observed_utc"):
            chunks.append(f"- Observation source : {_bucharest_time(source['observed_utc'])}.")
        if source.get("detail"):
            chunks.append(f"- Détail : {source['detail']}")
        chunks.append("")
        for item in current_items:
            if failed:
                retained_count += 1
                freshness = "Contenu connu conservé; source en échec, fraîcheur non vérifiée."
            elif fresh_keys is None:
                freshness = "Statut de fraîcheur non fourni dans le snapshot."
            elif (source_id, item["key"]) in fresh_keys:
                fresh_count += 1
                freshness = "Observé dans cette collecte."
            else:
                retained_count += 1
                freshness = "Item retenu; dernière observation individuelle inconnue."
            chunks.extend([f"#### {_clean_title(item['title'])}", "", freshness, "", f"Source : [ouvrir](<{_safe_url(item['url'])}>)", ""])
            if item.get("fingerprint"):
                chunks.extend([f"Empreinte : `{item['fingerprint']}`", ""])
            if item["body"]:
                chunks.extend([item["body"].rstrip("\r\n"), ""])
            else:
                chunks.extend(["_(Aucun texte fourni.)_", ""])

    chunks.extend([
        "## Limites et interprétation",
        "",
        "Un état de source en erreur est affiché comme erreur et ne signifie pas « aucun changement ». Les contenus inclus dans `items` restent visibles comme connus/retained, avec fraîcheur explicitée. Un statut `ok` sans item frais fourni n'établit pas à lui seul l'absence de nouveautés.",
        "",
        "Les textes des messages sont conservés tels que reçus; les titres sont seulement normalisés pour retirer les grands espaces. Les liens affichés sont limités aux URL HTTP(S) validées.",
        "",
    ])
    summary["fresh"] = fresh_count
    summary["retained"] = retained_count
    return "\n".join(chunks), summary


def _render_index(checked_utc: str, course_summaries: dict[str, dict], courses: dict) -> str:
    lines = [
        "---",
        f"managed_by: {MANAGED_BY}",
        f"checked_utc: '{checked_utc}'",
        "---",
        "# Veille — dernier contrôle",
        "",
        f"Dernière collecte : **{_bucharest_time(checked_utc)}**.",
        "",
        "Ces pages gérées automatiquement font foi pour le dernier contrôle des sources. Les synthèses révisées restent les notes de référence du cours; aucun contenu de ces synthèses n'est réécrit ici.",
        "",
        "| Cours | État | Items frais observés | Items retenus |",
        "|---|---|---:|---:|",
    ]
    for course_id in sorted(courses):
        summary = course_summaries[course_id]
        if summary["errors"]:
            status = f"{summary['errors']} erreur(s) de source"
        elif not summary["freshness_known"]:
            status = "Sources OK; fraîcheur non fournie"
        elif summary["fresh"] == 0:
            status = "Aucun changement signalé"
        else:
            status = "Sources OK"
        note_link = quote(f"Notes/{course_id}/{course_id} - Veille automatique.md", safe="/.-_")
        lines.append(f"| [{course_id}]({note_link}) — {courses[course_id]['label']} | {status} | {summary['fresh']} | {summary['retained']} |")
    lines.extend([
        "",
        "## Limites générales",
        "",
        "La collecte n'est pas une garantie d'exhaustivité. Les interfaces virtualisées peuvent retenir des éléments sans `last_seen` individuel; les éléments retenus ne sont pas présentés comme nouvellement vus. Teams couvre les publications/réponses chargées dans Général, sans fichiers non annoncés, autres canaux ou Classwork. Moodle respecte les limites de vérification des activités, forums et fichiers du collecteur. Les dates/heures sont rendues en heure de Bucarest à partir des horodatages UTC du snapshot.",
        "",
    ])
    return "\n".join(lines)


def update_vault(snapshot: object, vault: str | os.PathLike) -> list[Path]:
    """Render two levels of managed pages safely and atomically below an Obsidian vault."""
    data = validate_snapshot(snapshot)
    vault_path = Path(vault)
    outputs = []
    summaries = {}
    for course_id, course in data["courses"].items():
        note_relative = Path("Notes") / course_id / f"{course_id} - Veille automatique.md"
        note_path = _safe_path(vault_path, note_relative, create_parent=True)
        if note_path.exists():
            existing = note_path.read_text(encoding="utf-8")
            if not _frontmatter_managed(existing):
                raise ManualPageError(f"Page manuelle protégée: {note_path}")
        content, summaries[course_id] = _render_course(course_id, course, data["checked_utc"])
        outputs.append((note_path, content))

    index_path = _safe_path(vault_path, Path("Veille - dernier contrôle.md"))
    if index_path.exists():
        existing = index_path.read_text(encoding="utf-8")
        if not _frontmatter_managed(existing):
            raise ManualPageError(f"Page manuelle protégée: {index_path}")
    index = _render_index(data["checked_utc"], summaries, data["courses"])
    outputs.append((index_path, index))

    # Validate all target paths and ownership before writing any page.
    for path, _ in outputs:
        _safe_path(vault_path, path.relative_to(Path(vault_path).resolve()), create_parent=True)
        if path.exists() and not _frontmatter_managed(path.read_text(encoding="utf-8")):
            raise ManualPageError(f"Page manuelle protégée: {path}")
    for path, content in outputs:
        if not path.exists() or path.read_text(encoding="utf-8") != content:
            _atomic_write(path, content)
    return [path for path, _ in outputs]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot", help="JSON public du dernier contrôle cours-watch")
    parser.add_argument("vault", help="Chemin du coffre Obsidian")
    args = parser.parse_args()
    snapshot_path = Path(args.snapshot)
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    for path in update_vault(snapshot, args.vault):
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
