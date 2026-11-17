"""Characterize pipeline failures using synthetic data, without any remote service."""
import io
import json
import sys
from contextlib import redirect_stdout, redirect_stderr
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import cours_watch as watch
from course_reminders import resolve_task
from courswatch.settings import scan_command


class DocumentFreshnessTests(unittest.TestCase):
    def setUp(self):
        self.state = watch.State(':memory:')
        self.addCleanup(self.state.db.close)
        self.source = 'moodle:AUDIT'
        self.item = watch.Item('module:document', 'PDF', 'SHA256 ' + 'a' * 64, 'https://curs.upb.ro/')
        self.task = {'reference': {'source': self.source, 'key': self.item.key, 'document_sha256': 'a' * 64},
                     'date_mode': 'published_date', 'date': '2030-01-02'}
        self.now = datetime(2030, 1, 1, tzinfo=timezone.utc)

    def ingest(self, items, status='ok'):
        self.state.ingest(watch.Result(self.source, 'AUDIT', status, items))

    def resolved(self):
        return resolve_task(self.state, self.task, self.now, {})

    def test_pdf_error_invalidates_confirmation_without_erasing_old_bytes_reference(self):
        self.ingest([self.item])
        self.assertFalse(self.resolved()['unconfirmed'])
        self.ingest([watch.Item('module:document-status', 'PDF', 'Vérification indisponible', self.item.url)])
        self.assertTrue(self.resolved()['unconfirmed'])
        self.assertIn(self.item.body, self.state.db.execute('SELECT payload FROM items WHERE key=?', (self.item.key,)).fetchone()[0])

    def test_success_after_error_restores_confirmation(self):
        self.ingest([self.item])
        self.ingest([watch.Item('module:document-status', 'PDF', 'Erreur', self.item.url)])
        self.ingest([self.item])
        self.assertFalse(self.resolved()['unconfirmed'])

    def test_unobserved_document_is_not_fresh_even_within_the_same_second(self):
        self.ingest([self.item])
        self.ingest([])
        self.assertTrue(self.resolved()['unconfirmed'])

    def test_archive_failure_does_not_invalidate_successful_download(self):
        self.ingest([self.item, watch.Item('module:document-status', 'PDF', 'Archivage indisponible', self.item.url)])
        self.assertFalse(self.resolved()['unconfirmed'])

    def test_inaccessible_source_keeps_last_known_date_marked_stale(self):
        self.ingest([self.item])
        self.ingest([], 'network_error')
        self.assertTrue(self.resolved()['stale'])


class PipelineTests(unittest.TestCase):
    def test_discord_failure_preserves_ingestion_outbox_and_writes_report(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            item = watch.Item('new', 'Synthetic', 'value', 'https://example.invalid/')
            state = watch.State(root / 'state.db')
            state.ingest(watch.Result('teams:AUDIT', 'AUDIT', 'ok', []))
            state.db.close()
            with patch('cours_watch.load_discord_secrets', return_value={}), \
                 patch('cours_watch.notify', side_effect=RuntimeError('private secret')), \
                 patch('course_capture.publish_snapshot', return_value={'status': 'ok'}) as publish:
                report = watch.complete_scan({}, root, [watch.Result('teams:AUDIT', 'AUDIT', 'ok', [item])])
            self.assertEqual(report['delivery']['status'], 'error')
            self.assertEqual(report['status'], 'partial')
            self.assertNotIn('private secret', json.dumps(report))
            self.assertTrue((root / 'reports/latest.json').is_file())
            publish.assert_called_once()
            state = watch.State(root / 'state.db')
            try:
                self.assertEqual(state.db.execute('SELECT COUNT(*) FROM items').fetchone()[0], 1)
                self.assertEqual(len(state.pending()), 1)
            finally:
                state.db.close()

    def test_dry_scan_uses_disposable_profile_and_preserves_real_inputs(self):
        import asyncio
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)
            profile = source / 'browser-profile'
            profile.mkdir()
            original = profile / 'fixture'
            original.write_bytes(b'original')
            cookie = source / 'moodle-cookies.json'
            cookie.write_text('[]', encoding='utf-8')
            seen = []

            async def fake_scan(config, data, dry_run):
                seen.append(data)
                self.assertNotEqual(data, source)
                self.assertTrue(dry_run)
                (data / 'browser-profile/fixture').write_bytes(b'modified clone')
                return 0

            with patch('cours_watch._scan_browser', side_effect=fake_scan):
                self.assertEqual(asyncio.run(watch.scan({}, source, True)), 0)
            self.assertEqual(original.read_bytes(), b'original')
            self.assertEqual(cookie.read_text(), '[]')
            self.assertFalse(seen[0].exists())

    def test_dry_completion_never_opens_state_secrets_or_report_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch('cours_watch.State', side_effect=AssertionError('state accessed')), \
                 patch('cours_watch.load_discord_secrets', side_effect=AssertionError('secrets accessed')):
                report = watch.complete_scan({}, root, [], dry_run=True)
            self.assertTrue(report['dry_run'])
            self.assertEqual(list(root.iterdir()), [])

    def test_scan_command_transmits_bot_paths_explicitly(self):
        command = scan_command(Path('/isolated/data'), Path('/isolated/config.json'))
        self.assertEqual(command[-4:], ['--config', str(Path('/isolated/config.json')), '--data-dir', str(Path('/isolated/data'))])


class BrowserLoopTests(unittest.IsolatedAsyncioTestCase):
    async def test_mocked_browser_scan_collects_reports_progress_and_closes_context(self):
        data = Path('synthetic-browser-input')
        course = {'id': 'AUDIT', 'label': 'AUDIT'}
        config = {'courses': [course], 'timeout_ms': 1234}
        moodle_result = watch.Result('moodle:AUDIT', 'AUDIT', 'ok', [])
        teams_result = watch.Result('teams:AUDIT', 'AUDIT', 'ok', [])
        old_page = SimpleNamespace(close=AsyncMock())
        context = SimpleNamespace(new_page=AsyncMock(return_value=object()), pages=[old_page],
                                  cookies=AsyncMock(return_value=[]), close=AsyncMock())
        browser = SimpleNamespace(chromium=SimpleNamespace(
            launch_persistent_context=AsyncMock(return_value=context)))
        manager = MagicMock()
        manager.__aenter__ = AsyncMock(return_value=browser)
        manager.__aexit__ = AsyncMock(return_value=False)
        api = ModuleType('playwright.async_api')
        api.async_playwright = MagicMock(return_value=manager)
        package = ModuleType('playwright')
        package.async_api = api
        output, progress = io.StringIO(), io.StringIO()
        with patch.dict(sys.modules, {'playwright': package, 'playwright.async_api': api}), \
             patch.object(watch, 'moodle', AsyncMock(return_value=moodle_result)) as moodle, \
             patch.object(watch, 'teams', AsyncMock(return_value=teams_result)) as teams, \
             patch.object(watch, 'moodle_cookies', return_value=[]), \
             patch.object(watch, 'save_moodle_cookies') as save, \
             patch.object(watch, 'complete_scan', return_value={'status': 'ok'}) as complete, \
             redirect_stdout(output), redirect_stderr(progress):
            code = await watch._scan_browser(config, data, False)
        self.assertEqual(code, 0)
        moodle.assert_awaited_once_with(context, course, 1234, pdf_store=None)
        teams.assert_awaited_once_with(context, course, 1234)
        save.assert_called_once_with(data, [])
        complete.assert_called_once_with(config, data, [moodle_result, teams_result], dry_run=False)
        old_page.close.assert_awaited_once_with()
        context.close.assert_awaited_once_with()
        self.assertEqual(json.loads(output.getvalue()), {'status': 'ok'})
        self.assertEqual([json.loads(line)['progress'] for line in progress.getvalue().splitlines()],
                         ['moodle:AUDIT', 'teams:AUDIT'])
        self.assertTrue(browser.chromium.launch_persistent_context.await_args.kwargs['chromium_sandbox'])


if __name__ == '__main__':
    unittest.main()
