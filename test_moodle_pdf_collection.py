import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from cours_watch import Item, Result, State, moodle_pdf_items


class MoodlePdfCollectionTests(unittest.TestCase):
    def setUp(self):
        self.state = State(":memory:")
        self.course = {"id": "AAIT", "label": "AAIT"}
        self.row = {
            "key": "module-20524",
            "title": "DRL1: Value-based methods",
            "url": "https://curs.upb.ro/2026/mod/resource/view.php?id=20524",
            "body": "",
        }
        self.context = SimpleNamespace(request=object())

    def tearDown(self):
        self.state.db.close()

    def item_by_suffix(self, items, suffix):
        return next(item for item in items if item.key.endswith(suffix))

    def call(self, *, pdf_store=None, urls=None):
        return asyncio.run(moodle_pdf_items(self.context, self.course, self.row, pdf_store, urls))

    def seed(self, body):
        return self.state.ingest(Result("moodle:AAIT", "AAIT", "ok", [
            Item(self.row["key"], self.row["title"], "", self.row["url"]),
            Item(self.row["key"] + ":document", self.row["title"], body, self.row["url"]),
        ]))

    def test_same_pdf_fingerprint_body_creates_no_outbox_change(self):
        fingerprint = "SHA256 " + "a" * 64
        self.seed(fingerprint)
        fetched = {"sha256": "a" * 64}
        with patch("moodle_pdf_versions.fetch_pdf", new=AsyncMock(return_value=fetched)):
            items = self.call()
        self.assertEqual(self.item_by_suffix(items, ":document").body, fingerprint)
        self.assertEqual(self.state.ingest(Result("moodle:AAIT", "AAIT", "ok", [*items])), [])
        self.assertEqual(self.state.pending(), [])

    def test_first_document_fingerprint_is_quiet_and_uses_document_key(self):
        self.state.ingest(Result("moodle:AAIT", "AAIT", "ok", [
            Item(self.row["key"], self.row["title"], "", self.row["url"]),
        ]))
        fetched = {"sha256": "b" * 64}
        with patch("moodle_pdf_versions.fetch_pdf", new=AsyncMock(return_value=fetched)):
            items = self.call()
        document = self.item_by_suffix(items, ":document")
        self.assertEqual(document.key, "module-20524:document")
        self.assertEqual(document.body, "SHA256 " + "b" * 64)
        self.assertEqual(self.state.ingest(Result("moodle:AAIT", "AAIT", "ok", items)), [])
        self.assertEqual(self.state.pending(), [])

    def test_changed_hash_is_a_modified_item_at_the_same_url_and_title(self):
        old_fingerprint = "SHA256 " + "c" * 64
        self.seed(old_fingerprint)
        fetched = {"sha256": "d" * 64}
        with patch("moodle_pdf_versions.fetch_pdf", new=AsyncMock(return_value=fetched)):
            items = self.call()
        document = self.item_by_suffix(items, ":document")
        self.assertEqual(document.title, self.row["title"])
        self.assertEqual(document.url, self.row["url"])
        events = self.state.ingest(Result("moodle:AAIT", "AAIT", "ok", items))
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["kind"], "modified")
        self.assertEqual(events[0]["url"], self.row["url"])
        self.assertEqual(events[0]["title"], self.row["title"])

    def test_archive_failure_keeps_verified_hash_and_adds_status_item(self):
        self.seed("SHA256 " + "e" * 64)
        fetched = {"sha256": "f" * 64}
        with patch("moodle_pdf_versions.fetch_pdf", new=AsyncMock(return_value=fetched)), \
                patch("moodle_pdf_versions.archive_pdf", side_effect=RuntimeError("archive unavailable")):
            items = self.call(pdf_store="/spool")
        document = self.item_by_suffix(items, ":document")
        status = self.item_by_suffix(items, ":document-status")
        self.assertEqual(document.body, "SHA256 " + "f" * 64)
        self.assertEqual(status.body, "Empreinte PDF vérifiée, archivage des versions indisponible")
        self.assertEqual(document.url, self.row["url"])

        self.state.ingest(Result("moodle:AAIT", "AAIT", "ok", items))
        stored = self.state.db.execute(
            "SELECT payload FROM items WHERE source=? AND key=?",
            ("moodle:AAIT", document.key),
        ).fetchone()
        self.assertIn("SHA256 " + "f" * 64, stored[0])

    def test_download_failure_has_a_known_status_item_and_no_hash(self):
        with patch("moodle_pdf_versions.fetch_pdf", new=AsyncMock(side_effect=RuntimeError("download failed"))), \
                patch("moodle_pdf_versions.archive_pdf") as archive:
            items = self.call(pdf_store="/spool")
        status = self.item_by_suffix(items, ":document-status")
        self.assertEqual(status.key, "module-20524:document-status")
        self.assertEqual(status.body, "Vérification du contenu du fichier indisponible")
        self.assertFalse(any(item.key.endswith(":document") for item in items))
        archive.assert_not_called()

    def test_first_pdf_archives_under_module_key_for_baseline_comparison(self):
        first_url = "https://curs.upb.ro/pluginfile.php/123/mod_resource/content/1/slides.pdf"
        second_url = "https://curs.upb.ro/pluginfile.php/123/mod_resource/content/1/appendix.pdf"
        urls = [first_url, second_url]
        fetched = {"sha256": "1" * 64}
        with patch("moodle_pdf_versions.fetch_pdf", new=AsyncMock(return_value=fetched)), \
                patch("moodle_pdf_versions.archive_pdf") as archive:
            items = self.call(pdf_store="/spool", urls=urls)

        self.assertEqual(archive.call_count, 2)
        first_call, second_call = archive.call_args_list
        self.assertEqual(first_call.args[3], "module-20524")
        self.assertEqual(first_call.args[5], self.row["url"])
        self.assertNotEqual(second_call.args[3], "module-20524")
        self.assertEqual(len([item for item in items if item.key.endswith(":document")]), 2)


if __name__ == "__main__":
    unittest.main()
