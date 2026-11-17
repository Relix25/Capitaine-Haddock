"""Exercise the real /actualiser callback with a fully synthetic child process."""

import asyncio
import json
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from courswatch.settings import scan_command

import courswatch.discord_app.bot as course_bot
class RefreshTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        configuration = {"discord": {"guild_id": "1", "channel_id": "2", "role_ids": ["3"]}}
        self.enterContext(patch("courswatch.discord_app.bot.configuration", return_value=configuration))
        self.enterContext(patch("courswatch.discord_app.bot.read_json", return_value={}))
        self.spawn = self.enterContext(patch("courswatch.discord_app.bot.asyncio.create_subprocess_exec", new_callable=AsyncMock))
        self.bot = course_bot.CourseBot("synthetic-not-a-token")
        self.bot.authorize = AsyncMock(return_value=True)
        self.bot.write_status = MagicMock()
        self.bot.send_current = AsyncMock()
        self.bot.stop_child = AsyncMock()
        self.addAsyncCleanup(self.bot.close)
        self.interaction = SimpleNamespace(
            response=SimpleNamespace(defer=AsyncMock(), send_message=AsyncMock()),
            followup=SimpleNamespace(send=AsyncMock()),
        )
        self.callback = self.bot.tree.get_command("actualiser", guild=self.bot.scope).callback

    def child(self, report=None, *, output=None, returncode=0):
        if output is None:
            report = report if report is not None else {
                "status": "ok", "changes": [], "sources": [{"status": "ok"}],
                "delivery": {"status": "ok", "events_delivered": 0},
            }
            output = json.dumps(report).encode("utf-8")
        child = SimpleNamespace(returncode=returncode, communicate=AsyncMock(return_value=(output, b"")))
        self.spawn.return_value = child
        return child

    def last_scan(self):
        return self.bot.write_status.call_args.kwargs["last_manual_scan"]

    def assert_private(self):
        self.interaction.response.defer.assert_awaited_once_with(ephemeral=True, thinking=True)
        self.assertTrue(self.interaction.followup.send.await_args_list)
        self.assertTrue(all(call.kwargs.get("ephemeral") for call in self.interaction.followup.send.await_args_list))
        self.interaction.response.send_message.assert_not_awaited()

    async def test_default_collector_argv_uses_the_canonical_package(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(scan_command('fixture-data', 'fixture-config'),
                             ['/opt/cours-watch/venv/bin/python', '-m', 'courswatch', 'scan',
                              '--config', 'fixture-config', '--data-dir', 'fixture-data'])

    async def test_callback_passes_configured_paths_to_the_real_subprocess_boundary(self):
        self.child()
        data, config = Path("isolated fixture/data"), Path("isolated fixture/config.json")
        with patch("courswatch.discord_app.bot.DATA", data), patch("courswatch.discord_app.bot.CONFIG", config), patch.dict(os.environ, {
            "COURS_WATCH_PYTHON": "isolated fixture/python",
            "COURS_WATCH_COLLECTOR": "isolated fixture/collector.py",
        }):
            await self.callback(self.interaction)
        self.spawn.assert_awaited_once_with(
            "isolated fixture/python", "isolated fixture/collector.py", "scan",
            "--config", str(config), "--data-dir", str(data),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, start_new_session=True,
        )
        self.assertEqual(self.last_scan()["status"], "ok")
        self.bot.send_current.assert_awaited_once_with(self.interaction)
        self.assert_private()

    async def test_delivery_failure_with_healthy_sources_is_partial_and_keeps_reply_private(self):
        self.child({
            "status": "partial", "changes": [{"kind": "new"}], "sources": [{"status": "ok"}],
            "delivery": {"status": "error", "pending": 1},
        }, returncode=2)
        await self.callback(self.interaction)
        scan = self.last_scan()
        self.assertEqual(scan["status"], "partial")
        self.assertEqual((scan["sources_ok"], scan["sources_total"], scan["notifications"]), (1, 1, 0))
        text = self.interaction.followup.send.await_args.args[0]
        self.assertIn("en attente de livraison", text)
        self.assertNotIn("sont publiées", text)
        self.bot.send_current.assert_awaited_once_with(self.interaction)
        self.assert_private()

    async def test_busy_child_reports_skipped_and_shows_current_cached_point(self):
        self.child({"status": "busy", "action": "skipped_without_session_change"})
        await self.callback(self.interaction)
        self.assertEqual(self.last_scan()["status"], "skipped")
        self.assertEqual(self.last_scan()["reason"], "another_collection_running")
        self.bot.send_current.assert_awaited_once_with(self.interaction)
        self.assert_private()

    async def test_invalid_json_reports_failure_without_exposing_child_output(self):
        self.child(output=b"private synthetic output, not JSON")
        await self.callback(self.interaction)
        self.assertEqual(self.last_scan()["status"], "failed")
        self.assertEqual(self.last_scan()["reason"], "invalid_report")
        self.assertNotIn("private synthetic", str(self.interaction.followup.send.await_args))
        self.assertNotIn("private synthetic", str(self.bot.write_status.call_args_list))
        self.bot.send_current.assert_not_awaited()
        self.assert_private()

    async def test_spawn_failure_is_private_and_does_not_read_or_collect_more(self):
        self.spawn.side_effect = OSError("private synthetic path")
        await self.callback(self.interaction)
        self.assertEqual(self.last_scan()["status"], "failed")
        self.assertEqual(self.last_scan()["reason"], "start_failed")
        self.assertIsNone(self.bot.child)
        self.bot.send_current.assert_not_awaited()
        self.assertNotIn("private synthetic", str(self.interaction.followup.send.await_args))
        self.assertFalse(self.bot.refresh_lock.locked())
        self.assert_private()

    async def test_timeout_stops_the_child_and_releases_the_refresh_lock(self):
        child = self.child(returncode=None)
        child.communicate.side_effect = asyncio.TimeoutError
        await self.callback(self.interaction)
        self.bot.stop_child.assert_awaited_once_with()
        self.assertEqual(self.last_scan()["status"], "timeout")
        self.assertFalse(self.bot.refresh_lock.locked())
        self.bot.send_current.assert_not_awaited()
        self.assert_private()


if __name__ == "__main__":
    unittest.main()
