#!/usr/bin/env python3
"""Audit or stage a secret-free course vault seed for transfer to the VPS.

Default operation is read-only audit.  ``--apply`` creates a new staging tree
and a tar archive; it never modifies the source vault or overwrites outputs.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import sys
import tarfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Iterator
from urllib.parse import quote, unquote, urlsplit


COURSE_DIRS = (
    "Advanced artificial intelligence techniques",
    "AI for Trustworthy Decision Making",
    "Big-Data Fundamentals and Analytics",
    "Self-organizing systems",
    "Notes",
    "Projets",
)
ROOT_PEDAGOGICAL_SUFFIXES = {".md", ".markdown", ".json"}
EXCLUDED_DIR_NAMES = {
    ".git", ".obsidian", "outils", ".extraction", "__pycache__",
    ".cache", "cache", "venv", ".venv", "env", "node_modules",
    "asr_libs", "asr-libs", "asrlibs",
    "asr",
}
EXCLUDED_FILE_SUFFIXES = {
    ".pyc", ".pyo", ".pyd", ".so", ".dll", ".dylib",
    ".key", ".pem", ".p12", ".pfx", ".crt", ".cer",
}
EXCLUDED_NAME_PARTS = (
    "cookie", "storage-state", "storage_state", "auth", "credential",
    "secret", "token", "private-key", "private_key",
)
MAX_TOTAL_BYTES_DEFAULT = 1_000_000_000
MANIFEST_NAME = "Corpus - manifeste.json"

MD_LINK_RE = re.compile(
    r"(?<!!)\[[^\]]*\]\(\s*(?:<([^>]+)>|([^\s)]+))(?:\s+[^)]*)?\s*\)"
)
WIKILINK_RE = re.compile(r"!?\[\[([^\]]+)\]\]")


class SeedError(RuntimeError):
    pass


@dataclass(frozen=True)
class FileRecord:
    source: Path
    source_relative: str
    staged_relative: str
    original_size_bytes: int
    original_sha256: str
    rewrite_links: bool = False


def sha256_file(path: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            size += len(chunk)
            digest.update(chunk)
    return size, digest.hexdigest()


def is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def is_linklike(path: Path) -> bool:
    junction_check = getattr(path, "is_junction", None)
    return path.is_symlink() or bool(junction_check and junction_check())


def posix_relative(path: Path, root: Path) -> str:
    return PurePosixPath(*path.relative_to(root).parts).as_posix()


def is_excluded_name(name: str) -> bool:
    folded = name.casefold()
    return (
        folded in EXCLUDED_DIR_NAMES
        or folded == ".env"
        or folded.startswith(".env.")
        or any(part in folded for part in EXCLUDED_NAME_PARTS)
    )


def is_excluded_file(path: Path) -> bool:
    folded_name = path.name.casefold()
    return (
        folded_name in {".env", ".env.local", ".env.production"}
        or folded_name.startswith(".env.")
        or path.suffix.casefold() in EXCLUDED_FILE_SUFFIXES
        or any(part in folded_name for part in EXCLUDED_NAME_PARTS)
    )


def assert_no_symlink_components(path: Path, stop: Path) -> None:
    """Refuse symlink/junction components between an allowed root and path."""
    try:
        relative = path.relative_to(stop)
    except ValueError as exc:
        raise SeedError(f"Chemin hors périmètre autorisé: {path}") from exc
    cursor = stop
    if is_linklike(cursor):
        raise SeedError(f"Racine symbolique/jonction refusée: {cursor}")
    for part in relative.parts:
        cursor = cursor / part
        try:
            if is_linklike(cursor):
                raise SeedError(f"Lien symbolique/jonction refusé: {cursor}")
        except OSError as exc:
            raise SeedError(f"Impossible d’inspecter le chemin: {cursor}") from exc


def require_source_root(value: str) -> Path:
    root = Path(value).expanduser().absolute()
    if not root.is_dir():
        raise SeedError(f"Racine du vault absente ou non répertoire: {root}")
    if is_linklike(root):
        raise SeedError(f"Racine du vault symbolique refusée: {root}")
    return root.resolve(strict=True)


def walk_files(root: Path, base: Path) -> Iterator[Path]:
    """Walk a selected tree without following links; links abort the run."""
    assert_no_symlink_components(base, root)
    for current, dirs, files in os.walk(base, topdown=True, followlinks=False):
        current_path = Path(current)
        kept_dirs: list[str] = []
        for name in sorted(dirs, key=str.casefold):
            child = current_path / name
            if is_excluded_name(name):
                continue
            if is_linklike(child):
                raise SeedError(f"Répertoire symbolique/jonction refusé: {child}")
            kept_dirs.append(name)
        dirs[:] = kept_dirs
        for name in sorted(files, key=str.casefold):
            path = current_path / name
            if is_excluded_file(path):
                continue
            if is_linklike(path):
                raise SeedError(f"Fichier symbolique refusé: {path}")
            try:
                mode = path.stat(follow_symlinks=False).st_mode
            except OSError as exc:
                raise SeedError(f"Impossible d’inspecter le fichier: {path}") from exc
            if not stat.S_ISREG(mode):
                raise SeedError(f"Type de fichier non régulier refusé: {path}")
            yield path


def collect_primary_files(root: Path) -> dict[str, Path]:
    selected: dict[str, Path] = {}
    for name in COURSE_DIRS:
        base = root / name
        if not base.is_dir():
            raise SeedError(f"Dossier de cours requis absent: {name}")
        for path in walk_files(root, base):
            rel = posix_relative(path, root)
            selected[rel] = path
    for child in sorted(root.iterdir(), key=lambda item: item.name.casefold()):
        if child.is_dir():
            if child.name.casefold() in EXCLUDED_DIR_NAMES:
                continue
            continue
        if is_linklike(child):
            raise SeedError(f"Fichier racine symbolique refusé: {child}")
        if child.suffix.casefold() not in ROOT_PEDAGOGICAL_SUFFIXES:
            continue
        if is_excluded_file(child):
            continue
        rel = posix_relative(child, root)
        selected[rel] = child
    return selected


def local_link_target(raw_target: str, owner: Path, root: Path) -> Path | None:
    raw_target = raw_target.strip().strip("<>")
    if not raw_target or raw_target.startswith("#"):
        return None
    parts = urlsplit(raw_target)
    if parts.scheme or parts.netloc:
        return None
    target_text = unquote(parts.path).replace("\\", "/")
    if not target_text:
        return None
    candidate = Path(target_text)
    if candidate.is_absolute() or (len(target_text) > 1 and target_text[1] == ":"):
        return None
    lexical = Path(os.path.normpath(str(owner.parent / candidate)))
    lexical = Path(os.path.abspath(lexical))
    if not is_within(lexical, root):
        raise SeedError(f"Lien local sortant du vault refusé: {raw_target}")
    return lexical


def split_target_anchor(raw: str) -> tuple[str, str]:
    head, marker, anchor = raw.partition("#")
    return head, (marker + anchor if marker else "")


def resolve_wikilink(raw: str, owner: Path, root: Path,
                     markdown_index: dict[str, list[Path]]) -> Path | None:
    target = raw.split("|", 1)[0].strip()
    target, _ = split_target_anchor(target)
    if not target:
        return None
    candidate = local_link_target(target, owner, root)
    if candidate is not None:
        assert_no_symlink_components(candidate, root)
        options = [candidate]
        if not candidate.suffix:
            options += [candidate.with_suffix(".md"), candidate.with_suffix(".markdown")]
        for option in options:
            if option.is_file():
                return option
    if "/" not in target and "\\" not in target:
        stem = Path(target).stem.casefold()
        matches = markdown_index.get(stem, [])
        if len(matches) == 1:
            return matches[0]
    return None


def parse_markdown_targets(text: str, owner: Path, root: Path,
                           markdown_index: dict[str, list[Path]]) -> list[Path]:
    found: list[Path] = []
    for match in MD_LINK_RE.finditer(text):
        raw = match.group(1) or match.group(2) or ""
        target_path, _ = split_target_anchor(raw)
        candidate = local_link_target(target_path, owner, root)
        if candidate is not None:
            assert_no_symlink_components(candidate, root)
        if candidate is not None and candidate.is_file():
            found.append(candidate)
    for match in WIKILINK_RE.finditer(text):
        candidate = resolve_wikilink(match.group(1), owner, root, markdown_index)
        if candidate is not None and candidate.is_file():
            found.append(candidate)
    return found


def scan_evidence(root: Path, selected: dict[str, Path]) -> tuple[dict[Path, str], list[str], dict[Path, str]]:
    """Collect only explicitly linked .extraction files, recursively via MD."""
    markdown_index: dict[str, list[Path]] = {}
    for path in selected.values():
        if path.suffix.casefold() in {".md", ".markdown"}:
            markdown_index.setdefault(path.stem.casefold(), []).append(path)

    evidence: dict[Path, str] = {}
    unresolved_or_outside: set[str] = set()
    read_hashes: dict[Path, str] = {}
    queue = [p for p in selected.values() if p.suffix.casefold() in {".md", ".markdown"}]
    scanned: set[Path] = set()
    extraction_root = root / ".extraction"
    while queue:
        owner = queue.pop()
        if owner in scanned:
            continue
        scanned.add(owner)
        try:
            raw = owner.read_bytes()
            read_hashes[owner] = hashlib.sha256(raw).hexdigest()
            text = raw.decode("utf-8-sig")
        except (OSError, UnicodeError):
            continue
        for target in parse_markdown_targets(text, owner, root, markdown_index):
            if not is_within(target, extraction_root):
                continue
            assert_no_symlink_components(target, root)
            if is_excluded_file(target) or any(
                is_excluded_name(part) for part in target.relative_to(extraction_root).parts
            ):
                unresolved_or_outside.add(f"exclu sécurité: {target.relative_to(root).as_posix()}")
                continue
            if not target.is_file():
                unresolved_or_outside.add(f"cible absente: {target.relative_to(root).as_posix()}")
                continue
            suffix = target.relative_to(extraction_root).as_posix()
            existing = evidence.get(target)
            if existing is not None and existing != suffix:
                raise SeedError(f"Collision de cible Sources refusée: {suffix}")
            evidence[target] = suffix
            if target.suffix.casefold() in {".md", ".markdown"}:
                queue.append(target)
    return evidence, sorted(unresolved_or_outside), read_hashes


def safe_output_path(raw: str, root: Path, *, directory: bool) -> Path:
    path = Path(os.path.abspath(Path(raw).expanduser()))
    extraction_root = root / ".extraction"
    if is_within(path, root) and not is_within(path, extraction_root):
        raise SeedError("Une sortie dans le vault est permise uniquement sous .extraction (zone technique exclue).")
    stop = root if is_within(path, root) else path.anchor and Path(path.anchor)
    if not isinstance(stop, Path):
        raise SeedError(f"Chemin de sortie invalide: {path}")
    assert_no_symlink_components(path, stop)
    if path.exists():
        raise SeedError(f"La sortie existe déjà; aucun écrasement: {path}")
    parent = path.parent
    if not parent.is_dir():
        raise SeedError(f"Le dossier parent de sortie doit exister: {parent}")
    if directory and path == root:
        raise SeedError("La racine du vault ne peut pas être un dossier de staging.")
    return path


def build_plan(root: Path, max_total_bytes: int) -> tuple[list[FileRecord], list[str], dict[Path, str]]:
    selected = collect_primary_files(root)
    evidence, warnings, read_hashes = scan_evidence(root, selected)
    records: list[FileRecord] = []
    for rel, source in sorted(selected.items()):
        size, digest = sha256_file(source)
        if source in read_hashes and digest != read_hashes[source]:
            raise SeedError(f"Markdown modifié pendant son inventaire: {rel}")
        record = FileRecord(source, rel, rel, size, digest, source.suffix.casefold() in {".md", ".markdown"})
        records.append(record)
    for source, suffix in sorted(evidence.items(), key=lambda item: item[1].casefold()):
        rel = source.relative_to(root).as_posix()
        staged = PurePosixPath("Sources", *PurePosixPath(suffix).parts).as_posix()
        if staged in {rec.staged_relative for rec in records}:
            raise SeedError(f"Collision de chemin de staging refusée: {staged}")
        size, digest = sha256_file(source)
        if source in read_hashes and digest != read_hashes[source]:
            raise SeedError(f"Markdown de preuve modifié pendant son inventaire: {rel}")
        record = FileRecord(source, rel, staged, size, digest, source.suffix.casefold() in {".md", ".markdown"})
        records.append(record)
    total = sum(record.original_size_bytes for record in records)
    if total > max_total_bytes:
        raise SeedError(f"Corpus trop volumineux: {total} octets > plafond {max_total_bytes}.")
    records.sort(key=lambda rec: rec.staged_relative.casefold())
    if any(rec.staged_relative.casefold() == MANIFEST_NAME.casefold() for rec in records):
        raise SeedError(f"Le nom réservé au manifeste est déjà utilisé: {MANIFEST_NAME}")
    return records, warnings, evidence


def map_target_for_owner(target: Path, evidence: dict[Path, str], root: Path,
                         primary_paths: set[Path]) -> str | None:
    if target in evidence:
        suffix = evidence[target]
        return PurePosixPath("Sources", *PurePosixPath(suffix).parts).as_posix()
    if target in primary_paths:
        return posix_relative(target, root)
    return None


def rewrite_markdown(raw_bytes: bytes, record: FileRecord, root: Path,
                     evidence: dict[Path, str], primary_paths: set[Path],
                     markdown_index: dict[str, list[Path]]) -> bytes:
    bom = raw_bytes.startswith(b"\xef\xbb\xbf")
    text = raw_bytes.decode("utf-8-sig")

    def replace_markdown(match: re.Match[str]) -> str:
        raw_target = match.group(1) or match.group(2) or ""
        path_part, anchor = split_target_anchor(raw_target)
        candidate = local_link_target(path_part, record.source, root)
        if candidate is None:
            return match.group(0)
        staged_target = map_target_for_owner(candidate, evidence, root, primary_paths)
        if staged_target is None or candidate not in evidence:
            return match.group(0)
        owner_staged = PurePosixPath(record.staged_relative)
        rel_target = os.path.relpath(staged_target, str(owner_staged.parent)).replace("\\", "/")
        replacement = rel_target + anchor
        if match.group(1) is not None:
            return match.group(0).replace(f"<{match.group(1)}>", f"<{replacement}>", 1)
        encoded_replacement = quote(replacement, safe="/._-#")
        return match.group(0).replace(match.group(2), encoded_replacement, 1)

    def replace_wikilink(match: re.Match[str]) -> str:
        raw_content = match.group(1)
        target_text, separator, alias = raw_content.partition("|")
        target, anchor = split_target_anchor(target_text.strip())
        candidate = resolve_wikilink(target, record.source, root, markdown_index)
        if candidate is None or candidate not in evidence:
            return match.group(0)
        new_target = PurePosixPath("Sources", *PurePosixPath(evidence[candidate]).parts).as_posix()
        content = new_target + anchor
        if separator:
            content += "|" + alias
        return match.group(0).replace(raw_content, content, 1)

    text = MD_LINK_RE.sub(replace_markdown, text)
    text = WIKILINK_RE.sub(replace_wikilink, text)
    output = text.encode("utf-8")
    return (b"\xef\xbb\xbf" + output) if bom else output


def current_hash_guard(record: FileRecord) -> None:
    size, digest = sha256_file(record.source)
    if size != record.original_size_bytes or digest != record.original_sha256:
        raise SeedError(
            f"Source modifiée depuis lecture; écriture annulée pour {record.source_relative}"
        )


def write_exclusive(path: Path, data: bytes) -> tuple[int, str]:
    path.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(data).hexdigest()
    with path.open("xb") as handle:
        handle.write(data)
    return len(data), digest


def copy_checked(record: FileRecord, destination: Path) -> tuple[int, str]:
    current_hash_guard(record)
    destination.parent.mkdir(parents=True, exist_ok=True)
    source_digest = hashlib.sha256()
    size = 0
    with record.source.open("rb") as source, destination.open("xb") as target:
        while chunk := source.read(1024 * 1024):
            target.write(chunk)
            source_digest.update(chunk)
            size += len(chunk)
    copied_hash = source_digest.hexdigest()
    if size != record.original_size_bytes or copied_hash != record.original_sha256:
        raise SeedError(f"Hash source différent pendant copie: {record.source_relative}")
    return size, copied_hash


def create_tar(stage_root: Path, archive_path: Path, manifest_path: Path,
               records: list[FileRecord]) -> str:
    members = [stage_root.joinpath(*PurePosixPath(rec.staged_relative).parts) for rec in records]
    members.append(manifest_path)
    digest = hashlib.sha256()
    with archive_path.open("xb") as raw_archive:
        class HashingWriter:
            def write(self, data: bytes) -> int:
                digest.update(data)
                return raw_archive.write(data)

            def tell(self) -> int:
                return raw_archive.tell()

            def flush(self) -> None:
                raw_archive.flush()

        writer = HashingWriter()
        with tarfile.open(fileobj=writer, mode="w|", format=tarfile.PAX_FORMAT) as archive:
            for member in sorted(members, key=lambda p: p.relative_to(stage_root).as_posix().casefold()):
                arcname = member.relative_to(stage_root).as_posix()
                info = tarfile.TarInfo(arcname)
                info.size = member.stat().st_size
                info.mtime = 0
                info.uid = info.gid = 0
                info.uname = info.gname = ""
                info.mode = 0o644
                with member.open("rb") as handle:
                    archive.addfile(info, handle)
        writer.flush()
    return digest.hexdigest()


def audit(root: Path, records: list[FileRecord], warnings: list[str], show_files: bool) -> int:
    total = sum(rec.original_size_bytes for rec in records)
    print(f"Mode audit, aucune écriture. Racine logique: {root.name}/")
    print(f"Fichiers: {len(records)} | volume sélectionné: {total:,} octets ({total / 1_000_000:.1f} Mo décimaux)")
    proof_count = sum(rec.staged_relative.startswith("Sources/") for rec in records)
    print(f"Preuves locales sélectionnées sous Sources/: {proof_count}")
    md_count = sum(rec.rewrite_links for rec in records)
    print(f"Markdown inspectés pour liens locaux: {md_count}")
    print(f"Sorties de liens manquantes/exclues: {len(warnings)}")
    for warning in warnings:
        print(f"  - {warning}")
    if show_files:
        for rec in records:
            print(f"{rec.staged_relative}\t{rec.original_size_bytes}\t{rec.original_sha256}")
    return 0


def apply(root: Path, records: list[FileRecord], warnings: list[str],
          stage_root: Path, archive_path: Path, max_total_bytes: int) -> None:
    if stage_root == archive_path or is_within(archive_path, stage_root) or is_within(stage_root, archive_path):
        raise SeedError("L’archive et le staging doivent être des sorties disjointes.")
    if stage_root.exists() or archive_path.exists():
        raise SeedError("Staging ou archive existe déjà; aucun écrasement.")
    checksum_path = archive_path.with_name(archive_path.name + ".sha256")
    if checksum_path.exists():
        raise SeedError(f"Fichier checksum déjà présent; aucun écrasement: {checksum_path}")
    expected = sum(rec.original_size_bytes for rec in records)
    if expected > max_total_bytes:
        raise SeedError(f"Corpus trop volumineux: {expected} octets > {max_total_bytes}.")

    selected_paths = {rec.source for rec in records if not rec.staged_relative.startswith("Sources/")}
    evidence = {
        rec.source: PurePosixPath(rec.staged_relative).relative_to("Sources").as_posix()
        for rec in records if rec.staged_relative.startswith("Sources/")
    }
    markdown_index: dict[str, list[Path]] = {}
    for rec in records:
        if rec.source.suffix.casefold() in {".md", ".markdown"}:
            markdown_index.setdefault(rec.source.stem.casefold(), []).append(rec.source)

    stage_root.mkdir(parents=False, exist_ok=False)
    staged_entries: list[dict] = []
    for record in records:
        destination = stage_root.joinpath(*PurePosixPath(record.staged_relative).parts)
        if record.rewrite_links:
            current_hash_guard(record)
            raw = record.source.read_bytes()
            if hashlib.sha256(raw).hexdigest() != record.original_sha256:
                raise SeedError(f"Hash de note modifié depuis lecture: {record.source_relative}")
            rewritten = rewrite_markdown(raw, record, root, evidence, selected_paths, markdown_index)
            staged_size, staged_hash = write_exclusive(destination, rewritten)
        else:
            staged_size, staged_hash = copy_checked(record, destination)
        staged_entries.append({
            "path": record.staged_relative,
            "source_relative": record.source_relative,
            "staged_size_bytes": staged_size,
            "staged_sha256": staged_hash,
            "original_size_bytes": record.original_size_bytes,
            "original_sha256": record.original_sha256,
            "rewritten_markdown": record.rewrite_links,
        })

    staged_total = sum(entry["staged_size_bytes"] for entry in staged_entries)
    if staged_total > max_total_bytes:
        raise SeedError(f"Staging trop volumineux après réécriture: {staged_total} octets > {max_total_bytes}.")
    manifest = {
        "schema_version": 1,
        "created_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "selection": {
            "source_roots": list(COURSE_DIRS),
            "root_pedagogical_suffixes": sorted(ROOT_PEDAGOGICAL_SUFFIXES),
            "linked_extraction_evidence": True,
        },
        "files": staged_entries,
        "file_count": len(staged_entries),
        "total_staged_bytes": staged_total,
        "warnings": warnings,
    }
    manifest_path = stage_root / MANIFEST_NAME
    manifest_bytes = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    manifest_path.write_bytes(manifest_bytes)
    archive_sha = create_tar(stage_root, archive_path, manifest_path, records)
    with checksum_path.open("x", encoding="ascii", newline="\n") as handle:
        handle.write(f"{archive_sha}  {archive_path.name}\n")
    print(f"Staging créé: {stage_root}")
    print(f"Archive tar créée: {archive_path}")
    print(f"SHA256 archive: {archive_sha}")
    print(f"Manifeste: {stage_root / MANIFEST_NAME}")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=str(Path(__file__).resolve().parents[4]),
                        help="Racine du vault Cours (défaut historique: deux niveaux au-dessus du dépôt).")
    parser.add_argument("--max-total-bytes", type=int, default=MAX_TOTAL_BYTES_DEFAULT,
                        help="Plafond de taille source sélectionnée, défaut 1 000 000 000.")
    parser.add_argument("--show-files", action="store_true", help="Liste chemins, tailles et hashes en audit.")
    parser.add_argument("--apply", action="store_true", help="Créer staging et archive; sans cette option, audit seul.")
    parser.add_argument("--output-dir", help="Dossier de staging neuf, obligatoire avec --apply.")
    parser.add_argument("--archive", help="Chemin tar neuf, obligatoire avec --apply.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    try:
        root = require_source_root(args.root)
        records, warnings, _evidence = build_plan(root, args.max_total_bytes)
        existing_sources = root / "Sources"
        if existing_sources.exists():
            if is_linklike(existing_sources) or not existing_sources.is_dir():
                raise SeedError("Sources existe sous une forme inattendue; conflit refusé.")
            if any(existing_sources.iterdir()):
                raise SeedError("Sources existe déjà avec du contenu; conflit refusé, aucun écrasement.")
        if not args.apply:
            if args.output_dir or args.archive:
                raise SeedError("--output-dir et --archive nécessitent --apply; audit sans écriture.")
            return audit(root, records, warnings, args.show_files)
        if not args.output_dir or not args.archive:
            raise SeedError("--apply nécessite --output-dir et --archive explicites.")
        stage_root = safe_output_path(args.output_dir, root, directory=True)
        archive_path = safe_output_path(args.archive, root, directory=False)
        if archive_path.suffix.casefold() != ".tar":
            raise SeedError("Le chemin d’archive doit se terminer par .tar.")
        apply(root, records, warnings, stage_root, archive_path, args.max_total_bytes)
        return 0
    except (SeedError, OSError, tarfile.TarError) as exc:
        print(f"ERREUR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
