"""Stable identities across attachment reorderings, using synthetic PDF bytes."""
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest

from pypdf import PdfWriter

from courswatch.documents.identities import attachment_archive_keys
from moodle_pdf_versions import PdfArchiveError, _safe_catalog_key, archive_pdf


def pdf(pages):
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=72, height=72)
    stream = io.BytesIO()
    writer.write(stream)
    data = stream.getvalue()
    return {"bytes": data, "sha256": hashlib.sha256(data).hexdigest(), "pages": pages,
            "filename": "same-name.pdf", "final_url": "https://curs.upb.ro/2026/pluginfile.php/1/same-name.pdf"}


class AttachmentBindingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="haddock-bindings-")
        self.addCleanup(self.temp.cleanup)
        self.store = Path(self.temp.name)
        self.course = self.store / "AAIT"
        self.course.mkdir()
        self.module = "module-1"
        self.a = self.module + ":attachment-" + "a" * 12
        self.b = self.module + ":attachment-" + "b" * 12
        self.first, self.second = pdf(1), pdf(2)
        self.binding = self.course / "bindings" / _safe_catalog_key(self.module)

    def mapping(self, values):
        return attachment_archive_keys(self.store, "AAIT", self.module, values)

    def legacy(self, result):
        archive_pdf(result, self.store, "AAIT", self.module, "Synthetic PDF",
                    "https://curs.upb.ro/2026/mod/resource/view.php?id=1")

    def snapshot(self):
        return {path.relative_to(self.store).as_posix(): path.read_bytes()
                for path in self.store.rglob("*") if path.is_file()}

    def test_reordering_two_batches_keeps_the_same_primary(self):
        mapping, warning = self.mapping({self.b: self.second, self.a: self.first})
        self.assertIsNone(warning)
        self.assertEqual(mapping, {self.a: self.module, self.b: self.b})
        before = self.binding.read_bytes()
        mapping, warning = self.mapping({self.a: self.first, self.b: self.second})
        self.assertEqual(mapping, {self.a: self.module, self.b: self.b})
        self.assertIsNone(warning)
        self.assertEqual(self.binding.read_bytes(), before)

    def test_absent_primary_is_not_reassigned(self):
        self.mapping({self.a: self.first, self.b: self.second})
        before = self.binding.read_bytes()
        self.assertEqual(self.mapping({self.b: self.second}), ({self.b: self.b}, None))
        self.assertEqual(self.mapping({}), ({}, None))
        self.assertEqual(self.binding.read_bytes(), before)

    def test_unique_legacy_sha_selects_matching_attachment_even_when_not_first(self):
        self.legacy(self.second)
        before = self.snapshot()
        mapping, warning = self.mapping({self.a: self.first, self.b: self.second})
        self.assertEqual(mapping, {self.a: self.a, self.b: self.module})
        self.assertIsNone(warning)
        for name, data in before.items():
            self.assertEqual((self.store / name).read_bytes(), data)
        self.assertEqual(json.loads(self.binding.read_text())["primary"], self.b)

    def test_ambiguous_legacy_sha_freezes_catalog_without_guessing(self):
        self.legacy(self.first)
        before = self.snapshot()
        self.assertEqual(self.mapping({self.a: self.first, self.b: self.first}),
                         ({self.a: self.a, self.b: self.b}, "legacy_unresolved"))
        self.assertFalse(self.binding.exists())
        self.assertEqual(self.snapshot(), before)

    def test_changed_legacy_content_stays_frozen_without_unique_match(self):
        self.legacy(pdf(3))
        before = self.snapshot()
        self.assertEqual(self.mapping({self.a: self.first, self.b: self.second})[1], "legacy_unresolved")
        self.assertFalse(self.binding.exists())
        self.assertEqual(self.snapshot(), before)

    def test_existing_binding_survives_new_bytes_and_preserves_source_results(self):
        self.mapping({self.a: self.first})
        before = dict(self.second)
        self.assertEqual(self.mapping({self.a: self.second}), ({self.a: self.module}, None))
        self.assertEqual(self.second, before)

    def test_invalid_binding_fails_closed_without_replacement(self):
        self.mapping({self.a: self.first})
        for mutation in ({"version": 2}, {"course": "SOS"}, {"key": "other"}, {"primary": "unrelated"}):
            with self.subTest(mutation=mutation):
                value = {"version": 1, "course": "AAIT", "key": self.module, "primary": self.a, **mutation}
                self.binding.write_text(json.dumps(value))
                before = self.binding.read_bytes()
                with self.assertRaises(PdfArchiveError):
                    self.mapping({self.a: self.first})
                self.assertEqual(self.binding.read_bytes(), before)

    def test_invalid_inputs_do_not_create_archive_paths(self):
        for values in ({"../bad": self.first}, {self.a: {"sha256": "bad"}},
                       {self.a: {**self.first, "sha256": "0" * 64}}):
            with self.subTest(values=list(values)):
                with self.assertRaises(PdfArchiveError):
                    self.mapping(values)
                self.assertEqual(list(self.course.iterdir()), [])
        with self.assertRaises(PdfArchiveError):
            attachment_archive_keys(self.store, "../AAIT", self.module, {self.a: self.first})

    def test_symlinked_binding_is_rejected(self):
        (self.course / "bindings").mkdir()
        sentinel = self.store / "sentinel.json"
        sentinel.write_text("{}")
        try:
            self.binding.symlink_to(sentinel)
        except OSError:
            self.skipTest("Symbolic links unavailable")
        with self.assertRaises(PdfArchiveError):
            self.mapping({self.a: self.first})
        self.assertEqual(sentinel.read_text(), "{}")


if __name__ == "__main__":
    unittest.main()
