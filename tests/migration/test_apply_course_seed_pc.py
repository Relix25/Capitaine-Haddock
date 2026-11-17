"""Isolated fixtures for apply_course_seed_pc.py; never use the live vault."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PYTHON_EXE = Path(sys.executable)
SCRIPT = Path(__file__).resolve().parents[2] / "apply_course_seed_pc.py"
MANIFEST_NAME = "Corpus - manifeste.json"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class ApplyCourseSeedPcTests(unittest.TestCase):
    def setUp(self) -> None:
        if not PYTHON_EXE.is_file():
            self.skipTest(f"Runtime Python introuvable: {PYTHON_EXE}")
        self.temp = tempfile.TemporaryDirectory(prefix="apply_pc_")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / "vault"
        self.stage = self.base / "stage"
        self.root.mkdir()
        self.stage.mkdir()
        for folder in (
            "Advanced artificial intelligence techniques",
            "AI for Trustworthy Decision Making",
            "Big-Data Fundamentals and Analytics",
            "Self-organizing systems",
            "Notes",
        ):
            (self.root / folder).mkdir()
        self.course_rel = "Advanced artificial intelligence techniques/lecture.pdf"
        self.course_data = b"original course PDF"
        (self.root / self.course_rel).write_bytes(self.course_data)
        self.note_rel = "Notes/Actualisation.md"
        self.note_original = b"# Original\n[preuve](../.extraction/fixture/proof.md)\n"
        self.note_staged = b"# Actualise\n[preuve](../Sources/fixture/proof.md)\n"
        (self.root / self.note_rel).write_bytes(self.note_original)
        self.proof_rel = ".extraction/fixture/proof.md"
        self.proof_data = b"# Source de test\n"
        (self.root / self.proof_rel).parent.mkdir(parents=True)
        (self.root / self.proof_rel).write_bytes(self.proof_data)
        (self.stage / self.course_rel).parent.mkdir(parents=True)
        (self.stage / self.course_rel).write_bytes(self.course_data)
        (self.stage / self.note_rel).parent.mkdir(parents=True)
        (self.stage / self.note_rel).write_bytes(self.note_staged)
        (self.stage / "Sources/fixture").mkdir(parents=True)
        (self.stage / "Sources/fixture/proof.md").write_bytes(self.proof_data)
        entries = [
            self.entry(self.course_rel, self.course_rel, self.course_data, self.course_data),
            self.entry(self.note_rel, self.note_rel, self.note_staged, self.note_original),
            self.entry("Sources/fixture/proof.md", self.proof_rel, self.proof_data, self.proof_data),
        ]
        self.manifest_bytes = (json.dumps({"schema_version": 1, "files": entries}, indent=2) + "\n").encode()
        (self.stage / MANIFEST_NAME).write_bytes(self.manifest_bytes)

    @staticmethod
    def entry(path: str, source: str, staged: bytes, original: bytes) -> dict:
        return {
            "path": path,
            "source_relative": source,
            "staged_size_bytes": len(staged),
            "staged_sha256": sha(staged),
            "original_size_bytes": len(original),
            "original_sha256": sha(original),
        }

    def run_tool(self) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(PYTHON_EXE), "-X", "utf8", str(SCRIPT), "--root", str(self.root), "--stage", str(self.stage)],
            capture_output=True, text=True, encoding="utf-8", check=False,
        )

    def test_apply_creates_evidence_backup_and_is_idempotent(self) -> None:
        first = self.run_tool()
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual((self.root / self.course_rel).read_bytes(), self.course_data)
        self.assertEqual((self.root / self.note_rel).read_bytes(), self.note_staged)
        self.assertEqual(
            (self.root / ".extraction/Actualisation_2026-11-12/before_seed_rewrites" / self.note_rel).read_bytes(),
            self.note_original,
        )
        self.assertEqual((self.root / "Sources/fixture/proof.md").read_bytes(), self.proof_data)
        self.assertEqual((self.root / MANIFEST_NAME).read_bytes(), self.manifest_bytes)
        self.assertFalse((self.root / f"{MANIFEST_NAME}.sha256").exists())
        second = self.run_tool()
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertIn("0 copie(s), 0 note(s) réécrite(s)", second.stdout)

    def test_bad_staged_hash_aborts_before_any_write(self) -> None:
        (self.stage / "Sources/fixture/proof.md").write_bytes(b"tampered")
        result = self.run_tool()
        self.assertEqual(result.returncode, 2)
        self.assertEqual((self.root / self.note_rel).read_bytes(), self.note_original)
        self.assertFalse((self.root / "Sources").exists())
        self.assertFalse((self.root / MANIFEST_NAME).exists())

    def test_local_note_conflict_aborts_before_publishing_sources(self) -> None:
        (self.root / self.note_rel).write_bytes(b"local concurrent edit")
        result = self.run_tool()
        self.assertEqual(result.returncode, 2)
        self.assertFalse((self.root / "Sources").exists())
        self.assertFalse((self.root / MANIFEST_NAME).exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
