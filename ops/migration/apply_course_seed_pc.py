#!/usr/bin/env python3
"""Safely apply a prepared course seed to a PC vault after complete preflight."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from pathlib import Path, PurePosixPath

MANIFEST_NAME = "Corpus - manifeste.json"
BACKUP_RELATIVE = PurePosixPath(
    ".extraction/Actualisation_2026-11-12/before_seed_rewrites"
)
COURSE_ROOTS = (
    "Advanced artificial intelligence techniques",
    "AI for Trustworthy Decision Making",
    "Big-Data Fundamentals and Analytics",
    "Self-organizing systems",
)
HASH_RE = re.compile(r"^[0-9a-f]{64}$")


class SeedApplyError(RuntimeError):
    pass


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def is_linklike(path: Path) -> bool:
    junction_check = getattr(path, "is_junction", None)
    return path.is_symlink() or bool(junction_check and junction_check())


def assert_no_links_from_anchor(path: Path) -> None:
    current = Path(path.anchor)
    if is_linklike(current):
        raise SeedApplyError(f"Racine de volume symbolique/jonction refusée: {current}")
    for part in path.parts[1:]:
        current /= part
        if is_linklike(current):
            raise SeedApplyError(f"Lien symbolique/jonction refusé: {current}")


def assert_no_links(path: Path, root: Path) -> None:
    """Reject symlinks/junctions in every existing or prospective path component."""
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise SeedApplyError(f"Chemin hors racine autorisée: {path}") from exc
    current = root
    if is_linklike(current):
        raise SeedApplyError(f"Racine symbolique/jonction refusée: {current}")
    for part in relative.parts:
        current /= part
        if is_linklike(current):
            raise SeedApplyError(f"Lien symbolique/jonction refusé: {current}")


def safe_relative(value: object, label: str) -> PurePosixPath:
    if not isinstance(value, str) or not value or "\\" in value:
        raise SeedApplyError(f"Chemin relatif invalide dans {label}.")
    rel = PurePosixPath(value)
    if rel.is_absolute() or any(part in {"", ".", ".."} for part in rel.parts):
        raise SeedApplyError(f"Traversal ou chemin absolu refusé dans {label}: {value}")
    if rel.parts and ":" in rel.parts[0]:
        raise SeedApplyError(f"Chemin avec préfixe de volume refusé dans {label}: {value}")
    return rel


def checked_bytes(path: Path, root: Path, expected_hash: str, label: str) -> bytes:
    assert_no_links(path, root)
    if not path.is_file():
        raise SeedApplyError(f"Fichier requis absent: {label}")
    data = path.read_bytes()
    if digest(data) != expected_hash:
        raise SeedApplyError(f"SHA-256 inattendu: {label}")
    return data


def classify(path: PurePosixPath, source: PurePosixPath, original: str, staged: str) -> str:
    path_text = path.as_posix()
    source_text = source.as_posix()
    if any(path_text == name or path_text.startswith(name + "/") for name in COURSE_ROOTS):
        if source_text != path_text or original != staged:
            raise SeedApplyError(f"Un original de cours ne peut pas être réécrit: {path_text}")
        return "protected"
    if path_text.startswith("Sources/"):
        if not source_text.startswith(".extraction/"):
            raise SeedApplyError(f"Sources doit provenir de .extraction: {source_text}")
        return "source"
    if path_text == source_text and (
        path.parts[0] == "Notes" and path.suffix.casefold() in {".md", ".markdown"}
        or len(path.parts) == 1 and path.suffix.casefold() in {".md", ".markdown"}
    ):
        return "rewrite" if original != staged else "unchanged"
    if path_text != source_text or original != staged:
        raise SeedApplyError(f"Écriture hors périmètre autorisé: {path_text}")
    return "unchanged"


def backup_path(root: Path, relative: PurePosixPath) -> Path:
    return root.joinpath(*BACKUP_RELATIVE.parts, *relative.parts)


def atomic_new_file(path: Path, root: Path, data: bytes) -> None:
    assert_no_links(path, root)
    path.parent.mkdir(parents=True, exist_ok=True)
    assert_no_links(path, root)
    fd, temporary_name = tempfile.mkstemp(prefix=".course-seed-", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        if path.exists():
            raise SeedApplyError(f"Conflit concurrent, destination apparue: {path}")
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def atomic_replace(path: Path, root: Path, data: bytes,
                   expected_current_hash: str) -> None:
    assert_no_links(path, root)
    if not path.is_file() or digest(path.read_bytes()) != expected_current_hash:
        raise SeedApplyError(f"Conflit concurrent avant remplacement: {path}")
    fd, temporary_name = tempfile.mkstemp(prefix=".course-seed-", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        if digest(path.read_bytes()) != expected_current_hash:
            raise SeedApplyError(f"Conflit concurrent avant remplacement: {path}")
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def load_plan(root_arg: str, stage_arg: str) -> tuple[Path, Path, bytes, list[dict]]:
    root_input = Path(root_arg).expanduser().absolute()
    stage_input = Path(stage_arg).expanduser().absolute()
    assert_no_links_from_anchor(root_input)
    assert_no_links_from_anchor(stage_input)
    if not root_input.is_dir() or is_linklike(root_input):
        raise SeedApplyError("La racine du vault doit être un répertoire réel existant.")
    if not stage_input.is_dir() or is_linklike(stage_input):
        raise SeedApplyError("Le staging doit être un répertoire réel existant.")
    root = root_input.resolve(strict=True)
    stage = stage_input.resolve(strict=True)
    assert_no_links(stage, stage)
    manifest_path = stage / MANIFEST_NAME
    assert_no_links(manifest_path, stage)
    if not manifest_path.is_file():
        raise SeedApplyError("Manifeste absent du staging.")
    manifest_bytes = manifest_path.read_bytes()
    try:
        manifest = json.loads(manifest_bytes.decode("utf-8-sig"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise SeedApplyError("Manifeste illisible ou JSON invalide.") from exc
    if not isinstance(manifest, dict) or manifest.get("schema_version") != 1:
        raise SeedApplyError("Version de manifeste non prise en charge.")
    entries = manifest.get("files")
    if not isinstance(entries, list) or not entries:
        raise SeedApplyError("Le manifeste ne contient pas de liste files valide.")

    planned: list[dict] = []
    seen: set[str] = set()
    for index, entry in enumerate(entries):
        label = f"files[{index}]"
        if not isinstance(entry, dict):
            raise SeedApplyError(f"Entrée invalide: {label}")
        rel = safe_relative(entry.get("path"), f"{label}.path")
        source_rel = safe_relative(entry.get("source_relative"), f"{label}.source_relative")
        rel_key = rel.as_posix().casefold()
        if rel_key in seen or rel.as_posix() == MANIFEST_NAME:
            raise SeedApplyError(f"Chemin dupliqué ou réservé: {rel}")
        seen.add(rel_key)
        staged_hash = entry.get("staged_sha256")
        original_hash = entry.get("original_sha256")
        if not isinstance(staged_hash, str) or not HASH_RE.fullmatch(staged_hash):
            raise SeedApplyError(f"Hash final invalide: {label}")
        if not isinstance(original_hash, str) or not HASH_RE.fullmatch(original_hash):
            raise SeedApplyError(f"Hash source invalide: {label}")
        staged_path = stage.joinpath(*rel.parts)
        staged_data = checked_bytes(staged_path, stage, staged_hash, rel.as_posix())
        source_path = root.joinpath(*source_rel.parts)
        source_kind = classify(rel, source_rel, original_hash, staged_hash)
        if source_kind == "source":
            checked_bytes(source_path, root, original_hash, source_rel.as_posix())
            destination = root.joinpath(*rel.parts)
            assert_no_links(destination, root)
            if destination.exists():
                if not destination.is_file() or digest(destination.read_bytes()) != staged_hash:
                    raise SeedApplyError(f"Conflit Sources, destination différente: {rel}")
                action = "skip"
            else:
                action = "copy"
        elif source_kind == "rewrite":
            destination = root.joinpath(*rel.parts)
            assert_no_links(destination, root)
            backup = backup_path(root, rel)
            assert_no_links(backup, root)
            if destination.is_file() and digest(destination.read_bytes()) == staged_hash:
                if not backup.is_file() or digest(backup.read_bytes()) != original_hash:
                    raise SeedApplyError(f"Note déjà réécrite sans sauvegarde source valide: {rel}")
                action = "skip"
            elif destination.is_file() and digest(destination.read_bytes()) == original_hash:
                if backup.exists() and (not backup.is_file() or digest(backup.read_bytes()) != original_hash):
                    raise SeedApplyError(f"Conflit avec la sauvegarde existante: {backup}")
                action = "rewrite"
            else:
                raise SeedApplyError(f"Note absente ou modifiée depuis la préparation: {rel}")
        else:
            checked_bytes(source_path, root, original_hash, source_rel.as_posix())
            destination = root.joinpath(*rel.parts)
            assert_no_links(destination, root)
            if not destination.is_file() or digest(destination.read_bytes()) != original_hash:
                raise SeedApplyError(f"Fichier source inchangé absent ou modifié: {rel}")
            action = "skip"
        planned.append({
            "rel": rel, "source_rel": source_rel, "staged_data": staged_data,
            "staged_hash": staged_hash, "original_hash": original_hash,
            "kind": source_kind, "action": action,
        })

    root_manifest = root / MANIFEST_NAME
    assert_no_links(root_manifest, root)
    if root_manifest.exists():
        if not root_manifest.is_file() or digest(root_manifest.read_bytes()) != digest(manifest_bytes):
            raise SeedApplyError("Manifeste déjà publié avec un contenu différent.")
        manifest_action = "skip"
    else:
        manifest_action = "copy"
    return root, stage, manifest_bytes, planned + [{
        "rel": PurePosixPath(MANIFEST_NAME), "staged_data": manifest_bytes,
        "staged_hash": digest(manifest_bytes), "kind": "manifest", "action": manifest_action,
    }]


def apply_seed(root: Path, plan: list[dict]) -> tuple[int, int, int]:
    copied = rewritten = skipped = 0
    # Create and verify every requested backup before the first note replacement.
    for item in plan:
        if item["kind"] != "rewrite" or item["action"] != "rewrite":
            continue
        rel = item["rel"]
        destination = root.joinpath(*rel.parts)
        backup = backup_path(root, rel)
        assert_no_links(backup, root)
        backup.parent.mkdir(parents=True, exist_ok=True)
        assert_no_links(backup, root)
        original = destination.read_bytes()
        if digest(original) != item["original_hash"]:
            raise SeedApplyError(f"Conflit concurrent pendant sauvegarde: {rel}")
        if backup.exists():
            if not backup.is_file() or digest(backup.read_bytes()) != item["original_hash"]:
                raise SeedApplyError(f"Conflit avec la sauvegarde existante: {backup}")
        else:
            atomic_new_file(backup, root, original)

    for item in plan:
        action = item["action"]
        if action == "skip":
            skipped += 1
            continue
        destination = root.joinpath(*item["rel"].parts)
        if action == "rewrite":
            atomic_replace(destination, root, item["staged_data"], item["original_hash"])
            rewritten += 1
        elif action == "copy":
            atomic_new_file(destination, root, item["staged_data"])
            copied += 1
    return copied, rewritten, skipped


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, help="Racine du coffre PC cible.")
    parser.add_argument("--stage", required=True, help="Dossier de staging déjà préparé.")
    args = parser.parse_args(argv)
    try:
        root, _stage, _manifest, plan = load_plan(args.root, args.stage)
        copied, rewritten, skipped = apply_seed(root, plan)
        print(f"Seed appliqué: {copied} copie(s), {rewritten} note(s) réécrite(s), {skipped} déjà conformes.")
        return 0
    except (SeedApplyError, OSError) as exc:
        print(f"ERREUR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
