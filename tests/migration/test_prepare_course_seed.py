"""Controlled fixture tests for prepare_course_seed.py; never use the real vault."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path


PYTHON_EXE = Path(sys.executable)
SCRIPT = Path(__file__).resolve().parents[2] / "prepare_course_seed.py"
COURSE_DIRS = (
    "Advanced artificial intelligence techniques",
    "AI for Trustworthy Decision Making",
    "Big-Data Fundamentals and Analytics",
    "Self-organizing systems",
    "Notes",
    "Projets",
)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class PrepareCourseSeedTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not PYTHON_EXE.is_file():
            raise RuntimeError(f"Runtime Python introuvable à l’emplacement approuvé: {PYTHON_EXE}")
        cls.temp = tempfile.TemporaryDirectory(prefix="prepare-course-seed-tests-")
        cls.addClassCleanup(cls.temp.cleanup)
        cls.run_root = Path(cls.temp.name)
        cls.fixture_root = cls.run_root / "vault_fixture"
        cls.output_root = cls.run_root / "output"
        cls.fixture_root.mkdir(parents=True, exist_ok=False)
        cls.output_root.mkdir(parents=True, exist_ok=False)
        for dirname in COURSE_DIRS:
            (cls.fixture_root / dirname).mkdir(parents=True, exist_ok=False)

        (cls.fixture_root / ".extraction" / "fixture" / "teams").mkdir(parents=True)
        cls.note_path = cls.fixture_root / "Notes" / "Échéances cours.md"
        cls.note_bytes = (
            "# Notes de test\n\n"
            "[preuve](../.extraction/fixture/teams/evidence.md)\n"
        ).encode("utf-8")
        cls.note_path.write_bytes(cls.note_bytes)
        (cls.fixture_root / "Accueil Cours.md").write_text(
            "# Accueil\n\n[[Échéances cours]]\n", encoding="utf-8"
        )

        cls.evidence_path = cls.fixture_root / ".extraction" / "fixture" / "teams" / "evidence.md"
        cls.evidence_bytes = (
            "# Relevé de preuve\n\n"
            "[snapshot](<../snapshot frais.json>)\n"
        ).encode("utf-8")
        cls.evidence_path.write_bytes(cls.evidence_bytes)
        cls.snapshot_path = cls.fixture_root / ".extraction" / "fixture" / "snapshot frais.json"
        cls.snapshot_bytes = '{"cours":"SOS","date":"2026-11-12"}\n'.encode("utf-8")
        cls.snapshot_path.write_bytes(cls.snapshot_bytes)
        (cls.fixture_root / "Projets" / "Projet pédagogique.json").write_text(
            '{"purpose":"fixture"}\n', encoding="utf-8"
        )
        (cls.fixture_root / "Self-organizing systems" / "cours été.pdf").write_bytes(
            b"fixture PDF bytes"
        )

        # Fake credentials and caches: the fixture contains no real secrets.
        (cls.fixture_root / "Notes" / ".env").write_text("FAKE_TOKEN=not-a-secret\n", encoding="utf-8")
        (cls.fixture_root / "Notes" / "cookies.json").write_text('{"cookie":"fake"}', encoding="utf-8")
        (cls.fixture_root / "Notes" / "browser-storage-state.json").write_text(
            '{"fake":"state"}', encoding="utf-8"
        )
        (cls.fixture_root / "Notes" / "fixture.key").write_text("fake key", encoding="utf-8")
        (cls.fixture_root / "Notes" / "cache").mkdir()
        (cls.fixture_root / "Notes" / "cache" / "cached.json").write_text("{}", encoding="utf-8")
        (cls.fixture_root / "Projets" / "asr_libs" / "package").mkdir(parents=True)
        (cls.fixture_root / "Projets" / "asr_libs" / "package" / "module.pyc").write_bytes(b"fake cache")

        cls.stage_dir = cls.output_root / "stage"
        cls.archive_path = cls.output_root / "Cours seed UTF-8.tar"

    @classmethod
    def run_tool(cls, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(PYTHON_EXE), "-X", "utf8", str(SCRIPT), "--root", str(cls.fixture_root), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )

    def test_00_audit_excludes_technical_and_secret_fixtures(self) -> None:
        result = self.run_tool("--show-files")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Mode audit", result.stdout)
        self.assertIn("Sources/fixture/teams/evidence.md", result.stdout)
        self.assertIn("Sources/fixture/snapshot frais.json", result.stdout)
        for excluded in (".env", "cookies.json", "browser-storage-state.json", "fixture.key", "cached.json", "module.pyc"):
            self.assertNotIn(excluded, result.stdout)
        self.assertFalse(self.stage_dir.exists())
        self.assertFalse(self.archive_path.exists())

    def test_10_apply_rewrites_only_staging_and_records_hashes(self) -> None:
        original_note_hash = digest(self.note_path.read_bytes())
        original_evidence_hash = digest(self.evidence_path.read_bytes())
        result = self.run_tool(
            "--apply", "--output-dir", str(self.stage_dir), "--archive", str(self.archive_path)
        )
        self.assertEqual(result.returncode, 0, result.stderr)

        # Source originals stay byte-identical.
        self.assertEqual(digest(self.note_path.read_bytes()), original_note_hash)
        self.assertEqual(digest(self.evidence_path.read_bytes()), original_evidence_hash)
        self.assertEqual(self.note_path.read_bytes(), self.note_bytes)
        self.assertEqual(self.evidence_path.read_bytes(), self.evidence_bytes)

        staged_note = self.stage_dir / "Notes" / "Échéances cours.md"
        staged_evidence = self.stage_dir / "Sources" / "fixture" / "teams" / "evidence.md"
        staged_snapshot = self.stage_dir / "Sources" / "fixture" / "snapshot frais.json"
        self.assertIn(b"../Sources/fixture/teams/evidence.md", staged_note.read_bytes())
        self.assertIn(b"[snapshot](<../snapshot frais.json>)", staged_evidence.read_bytes())
        self.assertTrue(staged_snapshot.is_file())

        manifest_path = self.stage_dir / "Corpus - manifeste.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        entries = {entry["path"]: entry for entry in manifest["files"]}
        note_entry = entries["Notes/Échéances cours.md"]
        self.assertEqual(note_entry["source_relative"], "Notes/Échéances cours.md")
        self.assertEqual(note_entry["original_sha256"], original_note_hash)
        self.assertEqual(note_entry["staged_sha256"], digest(staged_note.read_bytes()))
        self.assertNotEqual(note_entry["original_sha256"], note_entry["staged_sha256"])
        self.assertEqual(entries["Sources/fixture/teams/evidence.md"]["original_sha256"], original_evidence_hash)
        self.assertEqual(entries["Sources/fixture/snapshot frais.json"]["staged_sha256"], digest(self.snapshot_bytes))
        self.assertFalse(any(Path(entry["path"]).is_absolute() for entry in manifest["files"]))
        self.assertFalse(any("\\" in entry["path"] for entry in manifest["files"]))
        self.assertFalse(any(".env" in entry["path"] or "cookies" in entry["path"] for entry in manifest["files"]))

        checksum = self.archive_path.with_name(self.archive_path.name + ".sha256")
        self.assertTrue(checksum.is_file())
        with tarfile.open(self.archive_path, "r:") as archive:
            names = archive.getnames()
            self.assertIn("Notes/Échéances cours.md", names)
            self.assertIn("Sources/fixture/teams/evidence.md", names)
            self.assertIn("Sources/fixture/snapshot frais.json", names)
            self.assertIn("Corpus - manifeste.json", names)
            self.assertFalse(any(name.startswith(".extraction/") for name in names))
            self.assertFalse(any("cookies" in name or name.endswith(".env") for name in names))

    def test_nonempty_sources_directory_is_refused(self) -> None:
        conflict_root = self.run_root / "vault_with_sources_conflict"
        conflict_root.mkdir()
        for dirname in COURSE_DIRS:
            (conflict_root / dirname).mkdir()
        (conflict_root / "Sources").mkdir()
        sentinel = conflict_root / "Sources" / "keep.txt"
        sentinel.write_text("keep", encoding="utf-8")
        stage = self.output_root / "conflict-stage"
        archive = self.output_root / "conflict.tar"
        result = subprocess.run(
            [str(PYTHON_EXE), "-X", "utf8", str(SCRIPT), "--root", str(conflict_root),
             "--apply", "--output-dir", str(stage), "--archive", str(archive)],
            capture_output=True, text=True, encoding="utf-8", check=False,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("Sources existe déjà avec du contenu", result.stderr)
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "keep")
        self.assertFalse(stage.exists())
        self.assertFalse(archive.exists())

    def test_existing_stage_is_not_overwritten(self) -> None:
        stage = self.output_root / "existing-stage"
        stage.mkdir()
        sentinel = stage / "keep.txt"
        sentinel.write_text("keep", encoding="utf-8")
        archive = self.output_root / "existing-stage.tar"
        result = self.run_tool("--apply", "--output-dir", str(stage), "--archive", str(archive))
        self.assertEqual(result.returncode, 2)
        self.assertIn("La sortie existe déjà", result.stderr)
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "keep")
        self.assertFalse(archive.exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
