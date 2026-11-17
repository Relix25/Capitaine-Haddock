import asyncio
import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

import moodle_pdf_versions as pdf_versions


def pdf_bytes(page_count, texts=None):
    writer = PdfWriter()
    texts = texts or [""] * page_count
    for text in texts:
        page = writer.add_blank_page(width=100, height=100)
        if text:
            font = DictionaryObject({
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject("/Helvetica"),
            })
            font_ref = writer._add_object(font)
            page[NameObject("/Resources")] = DictionaryObject({
                NameObject("/Font"): DictionaryObject({NameObject("/F1"): font_ref})
            })
            content = DecodedStreamObject()
            content.set_data(f"BT /F1 14 Tf 10 50 Td ({text}) Tj ET".encode("ascii"))
            page[NameObject("/Contents")] = writer._add_object(content)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


class FakeResponse:
    def __init__(self, body, url, *, headers=None, status=200):
        self._body = body
        self.url = url
        self.headers = headers or {}
        self.status = status
        self.disposed = False

    async def body(self):
        return self._body

    async def dispose(self):
        self.disposed = True


class FakeRequest:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if not self.responses:
            raise AssertionError("unexpected request")
        return self.responses.pop(0)


class StreamResponse:
    def __init__(self, body, url, *, headers=None, status=200):
        self.body_bytes = body
        self.url = url
        self.headers = headers or {}
        self.status = status
        self.position = 0
        self.read_sizes = []
        self.closed = False

    def geturl(self):
        return self.url

    def read(self, size):
        self.read_sizes.append(size)
        chunk = self.body_bytes[self.position:self.position + size]
        self.position += len(chunk)
        return chunk

    def close(self):
        self.closed = True


class StreamOpener:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def open(self, request, **kwargs):
        self.calls.append((request, kwargs))
        if not self.responses:
            raise AssertionError("unexpected streaming request")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class MoodlePdfVersionTests(unittest.TestCase):
    def stream_request(self, cookies=None):
        return SimpleNamespace(
            storage_state=AsyncMock(return_value={"cookies": cookies or [], "origins": []}),
            get=AsyncMock(side_effect=AssertionError("buffered transport must not be used")),
        )

    def test_streaming_context_stops_after_limit_plus_one_without_loading_body(self):
        url = "https://curs.upb.ro/2026/mod/resource/view.php?id=1"
        response = StreamResponse(b"%PDF-1.7\n" + b"x" * 100000, url)
        opener = StreamOpener([response])
        request = self.stream_request()
        with patch.object(pdf_versions, "build_opener", return_value=opener):
            with self.assertRaises(pdf_versions.PdfFetchError):
                asyncio.run(pdf_versions.fetch_pdf(request, url, max_bytes=32))
        self.assertEqual(response.position, 33)
        self.assertEqual(response.read_sizes, [33])
        self.assertTrue(response.closed)
        request.storage_state.assert_awaited_once_with()
        request.get.assert_not_awaited()

    def test_streaming_does_not_trust_a_small_declared_length(self):
        url = "https://curs.upb.ro/2026/mod/resource/view.php?id=1"
        response = StreamResponse(b"%PDF-1.7\n" + b"x" * 100000, url,
                                  headers={"content-length": "1"})
        with patch.object(pdf_versions, "build_opener", return_value=StreamOpener([response])):
            with self.assertRaises(pdf_versions.PdfFetchError):
                asyncio.run(pdf_versions.fetch_pdf(self.stream_request(), url, max_bytes=32))
        self.assertEqual(response.position, 33)
        self.assertTrue(response.closed)

    def test_streaming_rejects_declared_oversize_before_reading(self):
        url = "https://curs.upb.ro/2026/mod/resource/view.php?id=1"
        response = StreamResponse(b"unused", url, headers={"content-length": "999999"})
        with patch.object(pdf_versions, "build_opener", return_value=StreamOpener([response])):
            with self.assertRaises(pdf_versions.PdfFetchError):
                asyncio.run(pdf_versions.fetch_pdf(self.stream_request(), url, max_bytes=32))
        self.assertEqual(response.read_sizes, [])
        self.assertTrue(response.closed)

    def test_streaming_html_is_bounded_by_its_smaller_wrapper_limit(self):
        url = "https://curs.upb.ro/2026/mod/resource/view.php?id=1"
        response = StreamResponse(b"<html>" + b"x" * 100000, url,
                                  headers={"content-type": "text/html"})
        with patch.object(pdf_versions, "MAX_WRAPPER_BYTES", 32), \
             patch.object(pdf_versions, "build_opener", return_value=StreamOpener([response])):
            with self.assertRaises(pdf_versions.PdfFetchError):
                asyncio.run(pdf_versions.fetch_pdf(self.stream_request(), url, max_bytes=100000))
        self.assertEqual(response.position, 33)
        self.assertTrue(response.closed)

    def test_streaming_wrapper_and_same_host_redirect_preserve_pdf_bytes(self):
        url = "https://curs.upb.ro/2026/mod/resource/view.php?id=1"
        wrapper = "https://curs.upb.ro/2026/mod/resource/view.php?id=2"
        file_url = "https://curs.upb.ro/pluginfile.php/1/slides.pdf"
        data = pdf_bytes(2)
        responses = [
            StreamResponse(b"", url, status=302, headers={"location": wrapper}),
            StreamResponse(f'<object data="{file_url}"></object>'.encode(), wrapper,
                           headers={"content-type": "text/html"}),
            StreamResponse(data, file_url, headers={"content-type": "application/pdf"}),
        ]
        opener = StreamOpener(responses)
        with patch.object(pdf_versions, "build_opener", return_value=opener):
            result = asyncio.run(pdf_versions.fetch_pdf(self.stream_request(), url))
        self.assertEqual(result["bytes"], data)
        self.assertEqual(result["sha256"], hashlib.sha256(data).hexdigest())
        self.assertEqual(result["pages"], 2)
        self.assertEqual([call[0].full_url for call in opener.calls], [url, wrapper, file_url])
        self.assertTrue(all(response.closed for response in responses))

    def test_streaming_external_redirect_is_closed_without_following(self):
        url = "https://curs.upb.ro/2026/mod/resource/view.php?id=1"
        response = StreamResponse(b"unused", url, status=302,
                                  headers={"location": "https://evil.example/file.pdf"})
        opener = StreamOpener([response])
        with patch.object(pdf_versions, "build_opener", return_value=opener):
            with self.assertRaises(pdf_versions.PdfFetchError):
                asyncio.run(pdf_versions.fetch_pdf(self.stream_request(), url))
        self.assertEqual(len(opener.calls), 1)
        self.assertTrue(response.closed)
        self.assertEqual(response.read_sizes, [])

    def test_streaming_transport_errors_never_fall_back_to_buffered_get(self):
        url = "https://curs.upb.ro/2026/mod/resource/view.php?id=1"
        request = self.stream_request()
        opener = StreamOpener([OSError("synthetic connection error")])
        with patch.object(pdf_versions, "build_opener", return_value=opener):
            with self.assertRaises(pdf_versions.PdfFetchError):
                asyncio.run(pdf_versions.fetch_pdf(request, url))
        request.get.assert_not_awaited()

    def test_streaming_cookie_jar_preserves_path_expiry_and_moodle_scope(self):
        cookies = [
            {"name": "MoodleSession", "value": "synthetic", "domain": "curs.upb.ro", "path": "/2026/", "secure": True},
            {"name": "expired", "value": "synthetic", "domain": "curs.upb.ro", "path": "/", "expires": 1},
            {"name": "foreign", "value": "synthetic", "domain": "teams.example", "path": "/"},
            {"name": "parent", "value": "synthetic", "domain": ".upb.ro", "path": "/"},
        ]
        transport = pdf_versions._BoundedMoodleRequest(cookies)
        request = pdf_versions.Request("https://curs.upb.ro/2026/mod/resource/view.php?id=1")
        transport.jar.add_cookie_header(request)
        self.assertEqual(request.get_header("Cookie"), "MoodleSession=synthetic")
        other_path = pdf_versions.Request("https://curs.upb.ro/pluginfile.php/1/slides.pdf")
        transport.jar.add_cookie_header(other_path)
        self.assertIsNone(other_path.get_header("Cookie"))
        self.assertEqual(transport.cookie_updates(), [])

    def test_streaming_syncs_only_changed_authorized_cookies_to_same_context(self):
        url = "https://curs.upb.ro/2026/mod/resource/view.php?id=1"
        original = {"name": "MoodleSession", "value": "before", "domain": "curs.upb.ro", "path": "/", "httpOnly": True}
        context = SimpleNamespace(cookies=AsyncMock(return_value=[original]), add_cookies=AsyncMock())
        data = pdf_bytes(1)
        opener = StreamOpener([StreamResponse(data, url)])

        def factory(*handlers):
            jar = next(handler.cookiejar for handler in handlers if hasattr(handler, "cookiejar"))
            jar.set_cookie(pdf_versions._moodle_cookie({**original, "value": "after"}))
            return opener

        request = self.stream_request()
        with patch.object(pdf_versions, "build_opener", side_effect=factory):
            result = asyncio.run(pdf_versions.fetch_pdf(request, url, browser_context=context))
        self.assertEqual(result["bytes"], data)
        request.storage_state.assert_not_awaited()
        request.get.assert_not_awaited()
        context.add_cookies.assert_awaited_once()
        updates = context.add_cookies.await_args.args[0]
        self.assertEqual([(c["domain"], c["name"], c["value"]) for c in updates],
                         [("curs.upb.ro", "MoodleSession", "after")])

    def test_streaming_cookie_deletion_is_synchronized_without_other_domains(self):
        original = {"name": "MoodleSession", "value": "before", "domain": "curs.upb.ro", "path": "/"}
        transport = pdf_versions._BoundedMoodleRequest([original])
        transport.jar.clear("curs.upb.ro", "/", "MoodleSession")
        foreign = pdf_versions.Cookie(0, "foreign", "synthetic", None, False, "other.example", False,
                                      False, "/", True, True, None, True, None, None, {}, False)
        transport.jar.set_cookie(foreign)
        updates = transport.cookie_updates()
        self.assertEqual(len(updates), 1)
        self.assertEqual((updates[0]["name"], updates[0]["domain"], updates[0]["expires"]),
                         ("MoodleSession", "curs.upb.ro", 1))

    def test_streaming_closes_response_when_a_read_fails(self):
        url = "https://curs.upb.ro/2026/mod/resource/view.php?id=1"
        response = StreamResponse(b"unused", url)
        with patch.object(response, "read", side_effect=OSError("synthetic read error")), \
             patch.object(pdf_versions, "build_opener", return_value=StreamOpener([response])):
            with self.assertRaises(pdf_versions.PdfFetchError):
                asyncio.run(pdf_versions.fetch_pdf(self.stream_request(), url))
        self.assertTrue(response.closed)

    def test_streaming_rejects_a_read_that_finishes_after_total_deadline(self):
        url = "https://curs.upb.ro/2026/mod/resource/view.php?id=1"
        response = StreamResponse(b"unused", url)
        client = pdf_versions._BoundedMoodleRequest([])
        client.opener = StreamOpener([response])
        clock = [0]

        def late_read(size):
            clock[0] = 100
            return b""

        with patch.object(pdf_versions.time, "monotonic", side_effect=lambda: clock[0]), \
             patch.object(response, "read", side_effect=late_read):
            with self.assertRaisesRegex(pdf_versions.PdfFetchError, "Délai"):
                client.get_bounded(url, timeout=60000, max_bytes=100)
        self.assertTrue(response.closed)

    def test_same_url_and_filename_with_added_pages_creates_new_version(self):
        resource_url = "https://curs.upb.ro/2026/mod/resource/view.php?id=20524"
        final_url = "https://curs.upb.ro/pluginfile.php/1/mod_resource/content/2/slides.pdf"
        old_pdf, new_pdf = pdf_bytes(1), pdf_bytes(2)
        headers = {
            "content-type": "application/pdf",
            "content-disposition": 'attachment; filename="same-name.pdf"',
        }
        with tempfile.TemporaryDirectory() as tmp:
            store = Path(tmp)
            (store / "AAIT").mkdir()

            first_request = FakeRequest([FakeResponse(old_pdf, final_url, headers=headers)])
            first = asyncio.run(pdf_versions.fetch_pdf(first_request, resource_url))
            first_archive = pdf_versions.archive_pdf(first, store, "AAIT", "module-20524", "Slides", resource_url)
            self.assertFalse(first_archive["changed"])
            self.assertIsNone(first_archive["old_pages"])
            self.assertEqual(first_archive["new_pages"], 1)
            old_file = store / first_archive["relative_file"]
            self.assertEqual(old_file.read_bytes(), old_pdf)

            second_request = FakeRequest([FakeResponse(new_pdf, final_url, headers=headers)])
            second = asyncio.run(pdf_versions.fetch_pdf(second_request, resource_url))
            second_archive = pdf_versions.archive_pdf(second, store, "AAIT", "module-20524", "Slides", resource_url)
            self.assertTrue(second_archive["changed"])
            self.assertEqual((second_archive["old_pages"], second_archive["new_pages"]), (1, 2))
            self.assertEqual(old_file.read_bytes(), old_pdf)
            self.assertNotEqual(first["sha256"], second["sha256"])
            self.assertEqual(first["filename"], second["filename"])

            third_archive = pdf_versions.archive_pdf(second, store, "AAIT", "module-20524", "Slides", resource_url)
            self.assertFalse(third_archive["changed"])
            self.assertEqual((third_archive["old_pages"], third_archive["new_pages"]), (2, 2))
            catalog = json.loads(Path(second_archive["catalog_path"]).read_text(encoding="utf-8"))
            self.assertEqual(catalog["current_sha"], hashlib.sha256(new_pdf).hexdigest())
            self.assertEqual(len(catalog["versions"]), 2)

    def test_html_wrapper_follows_same_host_pluginfile_without_content_length(self):
        resource_url = "https://curs.upb.ro/2026/mod/resource/view.php?id=67666"
        file_url = "https://curs.upb.ro/pluginfile.php/2/mod_resource/content/1/guidelines.pdf"
        html = f'<html><body><object data="{file_url}"></object></body></html>'.encode()
        data = pdf_bytes(2)
        wrapper_response = FakeResponse(html, resource_url, headers={"content-type": "text/html; charset=utf-8"})
        file_response = FakeResponse(data, file_url, headers={
            "content-type": "application/octet-stream",
            "content-disposition": "attachment; filename*=UTF-8''guidelines%20new.pdf",
        })
        request = FakeRequest([
            wrapper_response,
            file_response,
        ])
        result = asyncio.run(pdf_versions.fetch_pdf(request, resource_url))

        self.assertEqual(result["bytes"], data)
        self.assertEqual(result["pages"], 2)
        self.assertEqual(result["filename"], "guidelines new.pdf")
        self.assertEqual(result["final_url"], file_url)
        self.assertEqual([call[0] for call in request.calls], [resource_url, file_url])
        self.assertTrue(all(call[1]["max_redirects"] == 0 for call in request.calls))
        self.assertTrue(wrapper_response.disposed)
        self.assertTrue(file_response.disposed)
        self.assertEqual(request.responses, [])

    def test_no_content_length_is_bounded_by_body_size_and_responses_are_disposed(self):
        url = "https://curs.upb.ro/2026/mod/resource/view.php?id=1"
        body = b"%PDF-1.7\n" + b"x" * 128
        response = FakeResponse(body, url, headers={"content-type": "application/pdf"})
        request = FakeRequest([response])
        with self.assertRaises(pdf_versions.PdfFetchError):
            asyncio.run(pdf_versions.fetch_pdf(request, url, max_bytes=32))
        self.assertTrue(response.disposed)

    def test_off_host_links_and_urls_are_never_requested(self):
        external_url = "https://evil.example/file.pdf"
        url = "https://curs.upb.ro/2026/mod/resource/view.php?id=2"
        html = f'<html><body><a href="{external_url}">download</a></body></html>'.encode()
        request = FakeRequest([FakeResponse(html, url, headers={"content-type": "text/html"})])
        with self.assertRaises(pdf_versions.PdfFetchError):
            asyncio.run(pdf_versions.fetch_pdf(request, url))
        self.assertEqual([call[0] for call in request.calls], [url])

        bad_request = FakeRequest([])
        with self.assertRaises(pdf_versions.PdfFetchError):
            asyncio.run(pdf_versions.fetch_pdf(bad_request, external_url))
        self.assertEqual(bad_request.calls, [])

        auth_url = "https://curs.upb.ro/2026/mod/resource/view.php?id=2&token=not-for-storage"
        with self.assertRaises(pdf_versions.PdfFetchError):
            asyncio.run(pdf_versions.fetch_pdf(bad_request, auth_url))
        self.assertEqual(bad_request.calls, [])

    def test_external_redirect_is_rejected_before_following(self):
        url = "https://curs.upb.ro/2026/mod/resource/view.php?id=2"
        response = FakeResponse(b"", url, status=302, headers={"location": "https://evil.example/file.pdf"})
        request = FakeRequest([response])
        with self.assertRaises(pdf_versions.PdfFetchError):
            asyncio.run(pdf_versions.fetch_pdf(request, url))
        self.assertEqual([call[0] for call in request.calls], [url])
        self.assertTrue(response.disposed)

    def test_catalog_course_path_is_safe_and_course_folder_must_exist(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Path(tmp)
            with self.assertRaises(pdf_versions.PdfArchiveError):
                pdf_versions.archive_pdf({}, store, "../AAIT", "key", "title", "https://curs.upb.ro/2026/mod/resource/view.php?id=1")
            with self.assertRaises(pdf_versions.PdfArchiveError):
                pdf_versions.archive_pdf({}, store, "AAIT", "key", "title", "https://curs.upb.ro/2026/mod/resource/view.php?id=1")

    def test_extract_added_pages_returns_trailing_page_text_slots(self):
        earlier, later = pdf_bytes(1), pdf_bytes(3, ["Old page", "New slide 1", "New slide 2"])
        added = pdf_versions.extract_added_pages(earlier, later)
        self.assertEqual([page["page_number"] for page in added], [2, 3])
        self.assertEqual([page["text"].strip() for page in added], ["New slide 1", "New slide 2"])
        self.assertEqual(pdf_versions.extract_added_pages(later, earlier), [])


if __name__ == "__main__":
    unittest.main()
