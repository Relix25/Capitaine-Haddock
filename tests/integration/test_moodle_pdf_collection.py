import asyncio
import hashlib
import tempfile
from pathlib import Path
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

    def test_attachment_catalog_binding_survives_reordering(self):
        first_url = "https://curs.upb.ro/pluginfile.php/123/mod_resource/content/1/slides.pdf"
        second_url = "https://curs.upb.ro/pluginfile.php/123/mod_resource/content/1/appendix.pdf"
        urls = [first_url, second_url]
        async def fetch(request, url, **kwargs):
            self.assertIs(kwargs['browser_context'], self.context)
            return {"sha256": ("1" if url == first_url else "2") * 64}
        with tempfile.TemporaryDirectory() as directory:
            store = Path(directory)
            (store / 'AAIT').mkdir()
            mappings = []
            with patch("moodle_pdf_versions.fetch_pdf", side_effect=fetch), \
                    patch("moodle_pdf_versions.archive_pdf") as archive:
                for order in (urls, list(reversed(urls))):
                    archive.reset_mock()
                    items = self.call(pdf_store=store, urls=order)
                    self.assertEqual(archive.call_count, 2)
                    mappings.append({call.args[0]['sha256']: call.args[3] for call in archive.call_args_list})
                    self.assertEqual(len([item for item in items if item.key.endswith(":document")]), 2)
            self.assertEqual(mappings[0], mappings[1])
            self.assertEqual(sum(value == self.row['key'] for value in mappings[0].values()), 1)

    def test_attachment_batch_stages_bytes_and_only_passes_metadata_to_binding(self):
        from courswatch.documents.identities import attachment_archive_keys
        urls = ['https://curs.upb.ro/pluginfile.php/1/first.pdf',
                'https://curs.upb.ro/pluginfile.php/1/second.pdf']
        bodies = {urls[0]: b'%PDF synthetic first', urls[1]: b'%PDF synthetic second'}
        archived, staged = [], []

        async def fetch(request, url, **kwargs):
            data = bodies[url]
            return {'bytes': data, 'sha256': hashlib.sha256(data).hexdigest(), 'pages': 1,
                    'filename': 'synthetic.pdf', 'final_url': url}

        def bind(store, course, module, pdfs):
            self.assertEqual(len(pdfs), 2)
            for metadata in pdfs.values():
                self.assertNotIn('bytes', metadata)
                path = metadata['_staged_pdf']
                self.assertTrue(path.is_file())
                self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), metadata['sha256'])
                staged.append(path)
            return attachment_archive_keys(store, course, module, pdfs)

        def archive(result, *args):
            self.assertNotIn('_staged_pdf', result)
            self.assertEqual(hashlib.sha256(result['bytes']).hexdigest(), result['sha256'])
            archived.append(result['bytes'])

        with tempfile.TemporaryDirectory() as directory:
            store = Path(directory)
            (store / 'AAIT').mkdir()
            with patch('moodle_pdf_versions.fetch_pdf', side_effect=fetch), \
                 patch('courswatch.documents.identities.attachment_archive_keys', side_effect=bind), \
                 patch('moodle_pdf_versions.archive_pdf', side_effect=archive):
                items = self.call(pdf_store=store, urls=urls)
            self.assertEqual(archived, list(bodies.values()))
            self.assertEqual(len([item for item in items if item.key.endswith(':document')]), 2)
            self.assertFalse(any(item.key.endswith(':document-status') for item in items))
            self.assertTrue(staged)
            self.assertFalse(any(path.exists() for path in staged))


if __name__ == "__main__":
    unittest.main()
