"""Fixture tests for the Moodle PDF publisher; never point these at the vault."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote, urlsplit

from course_pdf_publish import PublishError, publish_pdf_versions


PYTHON_EXE = Path(sys.executable)
FIXTURE_ROOT = Path(tempfile.mkdtemp(prefix="course-pdf-publish-tests-"))
RESOURCE_URL = "https://curs.upb.ro/2026/mod/resource/view.php?id=12345"
RESOURCE_KEY = "module-12345"
RESOURCE_TITLE = "Project Guidelines – version PDF"


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def catalog_filename(key: str) -> str:
    import re
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", key).strip(".-_")[:72] or "item"
    suffix = hashlib.sha256(key.encode("utf-8")).hexdigest()[:12]
    return f"{slug}-{suffix}.json"


def make_pdf(page_texts: list[str]) -> bytes:
    from pypdf import PdfWriter
    from pypdf.generic import (
        DecodedStreamObject,
        DictionaryObject,
        NameObject,
    )

    writer = PdfWriter()
    font = DictionaryObject({
        NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Helvetica"),
    })
    font_ref = writer._add_object(font)
    for text in page_texts:
        page = writer.add_blank_page(width=300, height=200)
        resources = DictionaryObject({
            NameObject("/Font"): DictionaryObject({NameObject("/F1"): font_ref}),
        })
        page[NameObject("/Resources")] = resources
        stream = DecodedStreamObject()
        safe_text = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        stream.set_data(f"BT /F1 12 Tf 30 100 Td ({safe_text}) Tj ET".encode("ascii"))
        page[NameObject("/Contents")] = writer._add_object(stream)
    import io
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def version_entry(course: str, body: bytes, pages: int, downloaded: str) -> dict:
    digest = sha256(body)
    return {
        "sha256": digest,
        "filename": "Project Guidelines.pdf",
        "pages": pages,
        "relative_file": f"{course}/files/{digest}.pdf",
        "bytes": len(body),
        "downloaded_utc": downloaded,
    }


def write_store(store: Path, versions: list[tuple[bytes, int, str]]) -> list[dict]:
    course = "AAIT"
    course_dir = store / course
    files_dir = course_dir / "files"
    catalog_dir = course_dir / "catalog"
    files_dir.mkdir(parents=True, exist_ok=True)
    catalog_dir.mkdir(parents=True, exist_ok=True)
    entries = []
    for body, pages, downloaded in versions:
        entry = version_entry(course, body, pages, downloaded)
        (files_dir / f"{entry['sha256']}.pdf").write_bytes(body)
        entries.append(entry)
    catalog = {
        "version": 1,
        "course": course,
        "key": RESOURCE_KEY,
        "title": RESOURCE_TITLE,
        "source_url": RESOURCE_URL,
        "current_sha": entries[-1]["sha256"],
        "versions": entries,
    }
    (catalog_dir / catalog_filename(RESOURCE_KEY)).write_text(
        json.dumps(catalog, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return entries


class CoursePdfPublishTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not PYTHON_EXE.is_file():
            raise RuntimeError(f"Runtime Python introuvable: {PYTHON_EXE}")
        FIXTURE_ROOT.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        cls.run_root = FIXTURE_ROOT / f"run_{stamp}"
        cls.run_root.mkdir()
        cls.store = cls.run_root / "store"
        cls.store.mkdir()
        cls.vault = cls.run_root / "vault"
        cls.vault.mkdir()

        cls.old_pdf = make_pdf(["ANCIENNE VERSION - PAGE 1"])
        cls.new_pdf = make_pdf(["VERSION ACTUELLE - PAGE 1", "VERSION ACTUELLE - PAGE 2"])
        cls.entries = write_store(cls.store, [
            (cls.old_pdf, 1, "2026-11-11T08:00:00Z"),
            (cls.new_pdf, 2, "2026-11-12T08:00:00Z"),
        ])

    def test_00_publish_keeps_same_name_versions_and_extracts_all_pages(self) -> None:
        summary = publish_pdf_versions(self.store, self.vault)
        self.assertEqual(summary["courses"], ["AAIT"])
        self.assertEqual(summary["resources"], 1)
        self.assertEqual(summary["versions"], 2)
        self.assertEqual(summary["pdfs_copied"], 2)
        self.assertEqual(summary["text_notes_created"], 2)
        self.assertEqual(summary["indexes_updated"], 1)

        old_sha, new_sha = (entry["sha256"] for entry in self.entries)
        old_pdf_path = self.vault / "Documents - versions" / "AAIT" / f"{old_sha}.pdf"
        new_pdf_path = self.vault / "Documents - versions" / "AAIT" / f"{new_sha}.pdf"
        self.assertEqual(old_pdf_path.read_bytes(), self.old_pdf)
        self.assertEqual(new_pdf_path.read_bytes(), self.new_pdf)
        stored_old = self.store / "AAIT" / "files" / f"{old_sha}.pdf"
        stored_new = self.store / "AAIT" / "files" / f"{new_sha}.pdf"
        self.assertEqual(stored_old.read_bytes(), self.old_pdf)
        self.assertEqual(stored_new.read_bytes(), self.new_pdf)
        self.assertEqual(sha256(old_pdf_path.read_bytes()), old_sha)
        self.assertEqual(sha256(new_pdf_path.read_bytes()), new_sha)

        old_note = self.vault / "Notes" / "AAIT" / "Documents" / f"{old_sha}.md"
        new_note = self.vault / "Notes" / "AAIT" / "Documents" / f"{new_sha}.md"
        old_text = old_note.read_text(encoding="utf-8")
        new_text = new_note.read_text(encoding="utf-8")
        self.assertIn("managed_by: cours-watch", old_text)
        self.assertIn("ANCIENNE VERSION - PAGE 1", old_text)
        self.assertIn("VERSION ACTUELLE - PAGE 1", new_text)
        self.assertIn("VERSION ACTUELLE - PAGE 2", new_text)
        self.assertIn("### Page 1", new_text)
        self.assertIn("### Page 2", new_text)
        self.assertIn("#page=2", new_text)

        index = self.vault / "Notes" / "AAIT" / "AAIT - Versions des PDF.md"
        index_text = index.read_text(encoding="utf-8")
        self.assertIn("managed_by: cours-watch", index_text)
        self.assertIn("Project Guidelines – version PDF", index_text)
        self.assertIn("+1", index_text)
        self.assertIn(old_sha[:12], index_text)
        self.assertIn(new_sha[:12], index_text)
        self.assertIn("**courante**", index_text)
        self.assert_local_markdown_links_resolve(index)
        self.assert_local_markdown_links_resolve(old_note)
        self.assert_local_markdown_links_resolve(new_note)

        for path in (old_pdf_path, new_pdf_path, old_note, new_note, index):
            mode = stat.S_IMODE(path.stat().st_mode)
            self.assertEqual(mode & 0o660, 0o660, f"mode groupe attendu pour {path.name}: {oct(mode)}")

        # Temporary publication files are cleaned up; no historical outputs are removed.
        self.assertFalse(any(path.name.startswith(".cours-watch-") for path in self.vault.rglob("*")))

    def assert_local_markdown_links_resolve(self, markdown_path: Path) -> None:
        text = markdown_path.read_text(encoding="utf-8")
        targets = []
        for match in re.finditer(r"\]\((?:<([^>]+)>|([^\s)]+))(?:\s+[^)]*)?\)", text):
            target = match.group(1) or match.group(2)
            parsed = urlsplit(target)
            if parsed.scheme or parsed.netloc:
                continue
            target_path = unquote(parsed.path)
            if target_path:
                targets.append(target_path)
        self.assertTrue(targets, f"pas de lien Markdown trouvé dans {markdown_path.name}")
        for target in targets:
            resolved = (markdown_path.parent / target).resolve()
            self.assertTrue(resolved.is_file(), f"lien local cassé: {markdown_path.name} -> {target}")

    def test_10_rerun_is_idempotent_and_preserves_existing_versions(self) -> None:
        before = {
            path.relative_to(self.vault).as_posix(): (path.read_bytes(), path.stat().st_mtime_ns)
            for path in self.vault.rglob("*") if path.is_file()
        }
        summary = publish_pdf_versions(self.store, self.vault)
        self.assertEqual(summary["pdfs_copied"], 0)
        self.assertEqual(summary["text_notes_created"], 0)
        self.assertEqual(summary["text_notes_updated"], 0)
        self.assertEqual(summary["indexes_updated"], 0)
        after = {
            path.relative_to(self.vault).as_posix(): (path.read_bytes(), path.stat().st_mtime_ns)
            for path in self.vault.rglob("*") if path.is_file()
        }
        self.assertEqual(after, before)

    def test_20_renamed_resource_refreshes_managed_notes_and_index_for_same_sha(self) -> None:
        old_sha, new_sha = (entry["sha256"] for entry in self.entries)
        pdf_paths = [
            self.vault / "Documents - versions" / "AAIT" / f"{digest}.pdf"
            for digest in (old_sha, new_sha)
        ]
        before_pdf = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in pdf_paths}

        catalog_path = next((self.store / "AAIT" / "catalog").glob("*.json"))
        catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
        catalog["title"] = "Project Guidelines — renamed Moodle resource"
        catalog_path.write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        summary = publish_pdf_versions(self.store, self.vault)
        self.assertEqual(summary["pdfs_copied"], 0)
        self.assertEqual(summary["text_notes_created"], 0)
        self.assertEqual(summary["text_notes_updated"], 2)
        self.assertEqual(summary["indexes_updated"], 1)
        for path, original in before_pdf.items():
            self.assertEqual((path.read_bytes(), path.stat().st_mtime_ns), original)

        index = self.vault / "Notes" / "AAIT" / "AAIT - Versions des PDF.md"
        self.assertIn("Project Guidelines — renamed Moodle resource", index.read_text(encoding="utf-8"))
        for digest in (old_sha, new_sha):
            note = self.vault / "Notes" / "AAIT" / "Documents" / f"{digest}.md"
            note_text = note.read_text(encoding="utf-8")
            self.assertIn("Project Guidelines — renamed Moodle resource", note_text)
            self.assertIn("managed_by: cours-watch", note_text)
            self.assert_local_markdown_links_resolve(note)
        self.assert_local_markdown_links_resolve(index)

    def test_manual_page_blocks_all_publishing(self) -> None:
        old_sha = self.entries[0]["sha256"]
        vault = self.run_root / "vault_manual_page"
        vault.mkdir()
        manual = vault / "Notes" / "AAIT" / "Documents" / f"{old_sha}.md"
        manual.parent.mkdir(parents=True)
        manual.write_text("# Page manuelle\n", encoding="utf-8")
        with self.assertRaisesRegex(PublishError, "sans marqueur"):
            publish_pdf_versions(self.store, vault)
        self.assertEqual(manual.read_text(encoding="utf-8"), "# Page manuelle\n")
        self.assertFalse((vault / "Documents - versions").exists())

    def test_preexisting_different_pdf_is_preserved_and_refused_before_writes(self) -> None:
        old_sha = self.entries[0]["sha256"]
        vault = self.run_root / "vault_pdf_conflict"
        dest = vault / "Documents - versions" / "AAIT" / f"{old_sha}.pdf"
        dest.parent.mkdir(parents=True)
        sentinel = b"not the versioned PDF"
        dest.write_bytes(sentinel)
        with self.assertRaisesRegex(PublishError, "Conflit PDF préexistant"):
            publish_pdf_versions(self.store, vault)
        self.assertEqual(dest.read_bytes(), sentinel)
        self.assertFalse((vault / "Notes").exists())

    def test_unexpected_course_id_is_rejected(self) -> None:
        store = self.run_root / "store_unknown"
        store.mkdir()
        (store / "UNKNOWN").mkdir()
        vault = self.run_root / "vault_unknown"
        vault.mkdir()
        with self.assertRaisesRegex(PublishError, "Identifiant de cours non prévu"):
            publish_pdf_versions(store, vault)
        self.assertEqual(list(vault.iterdir()), [])

    def test_cli_accepts_the_two_store_and_vault_arguments(self) -> None:
        store = self.run_root / "store_cli"
        write_store(store, [(self.old_pdf, 1, "2026-11-11T08:00:00Z")])
        vault = self.run_root / "vault_cli"
        vault.mkdir()
        result = subprocess.run(
            [str(PYTHON_EXE), "-X", "utf8", str(Path(__file__).with_name("course_pdf_publish.py")),
             str(store), str(vault)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["versions"], 1)

    def test_symlinked_version_is_refused_when_platform_allows_links(self) -> None:
        store = self.run_root / "store_symlink"
        course_dir = store / "AAIT"
        files_dir = course_dir / "files"
        catalog_dir = course_dir / "catalog"
        files_dir.mkdir(parents=True)
        catalog_dir.mkdir()
        entry = version_entry("AAIT", self.old_pdf, 1, "2026-11-11T08:00:00Z")
        outside = self.run_root / "outside.pdf"
        outside.write_bytes(self.old_pdf)
        linked = files_dir / f"{entry['sha256']}.pdf"
        try:
            os.symlink(outside, linked)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"Symlink creation unavailable in this Windows context: {exc}")
        catalog = {
            "version": 1,
            "course": "AAIT",
            "key": RESOURCE_KEY,
            "title": RESOURCE_TITLE,
            "source_url": RESOURCE_URL,
            "current_sha": entry["sha256"],
            "versions": [entry],
        }
        (catalog_dir / catalog_filename(RESOURCE_KEY)).write_text(
            json.dumps(catalog), encoding="utf-8"
        )
        vault = self.run_root / "vault_symlink"
        vault.mkdir()
        with self.assertRaisesRegex(PublishError, "Lien symbolique"):
            publish_pdf_versions(store, vault)
        self.assertEqual(list(vault.iterdir()), [])

    def test_invalid_relative_file_and_external_url_are_rejected(self) -> None:
        store = self.run_root / "store_invalid"
        entries = write_store(store, [(self.new_pdf, 2, "2026-11-12T08:00:00Z")])
        catalog_path = next((store / "AAIT" / "catalog").glob("*.json"))
        catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
        catalog["versions"][0]["relative_file"] = "../outside.pdf"
        catalog_path.write_text(json.dumps(catalog), encoding="utf-8")
        vault = self.run_root / "vault_invalid"
        vault.mkdir()
        with self.assertRaisesRegex(PublishError, "relative_file"):
            publish_pdf_versions(store, vault)
        self.assertEqual(list(vault.iterdir()), [])

        catalog["versions"][0]["relative_file"] = entries[0]["relative_file"]
        catalog["source_url"] = "https://example.com/course.pdf"
        catalog_path.write_text(json.dumps(catalog), encoding="utf-8")
        with self.assertRaisesRegex(PublishError, "source_url"):
            publish_pdf_versions(store, vault)
        self.assertEqual(list(vault.iterdir()), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
