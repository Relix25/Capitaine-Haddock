import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cours_watch import Item, Result, State, import_cookies, notify, format_notification, moodle_cookies, save_moodle_cookies, canonical_forum_url, load_discord_secrets


class StateTests(unittest.TestCase):
    def setUp(self):
        self.state = State(':memory:')

    def tearDown(self):
        self.state.db.close()

    def result(self, body='v1', key='message-1'):
        return Result('teams:AAIT', 'AAIT', 'ok', [Item(key, 'Deadline', body, 'https://teams.cloud.microsoft/')])

    def test_first_success_is_quiet_baseline(self):
        self.assertEqual(self.state.ingest(self.result()), [])
        self.assertEqual(self.state.pending(), [])

    def test_new_and_edited_posts_are_not_repeated(self):
        self.state.ingest(self.result())
        self.assertEqual(self.state.ingest(self.result('v2'))[0]['kind'], 'modified')
        self.assertEqual(self.state.ingest(self.result('v2')), [])
        self.assertEqual(self.state.ingest(self.result('v2', 'message-2'))[0]['kind'], 'new')

    def test_network_failure_does_not_erase_baseline(self):
        self.state.ingest(self.result())
        failure = Result('teams:AAIT', 'AAIT', 'network_error', [], 'Unavailable')
        self.assertEqual(len(self.state.ingest(failure)), 1)
        self.assertEqual(self.state.ingest(failure), [])
        self.assertEqual(self.state.db.execute('SELECT COUNT(*) FROM items').fetchone()[0], 1)
        self.assertEqual(self.state.ingest(self.result())[0]['kind'], 'source_recovered')

    def test_text_reversion_is_a_real_change(self):
        self.state.ingest(self.result())
        self.state.ingest(self.result('v2'))
        self.assertEqual(self.state.ingest(self.result('v1'))[0]['kind'], 'modified')

    def test_unconfigured_discord_retains_events(self):
        self.state.ingest(self.result())
        self.state.ingest(self.result('v2'))
        self.assertEqual(notify(self.state, {'mode': 'disabled'}, {})['pending'], 1)

    def test_delivery_failure_keeps_outbox_and_filters_mentions(self):
        self.state.ingest(self.result())
        self.state.ingest(self.result('@everyone malicious content'))
        configuration = {'mode': 'webhook', 'user_ids': ['123456789012345678'], 'role_ids': []}
        secrets = {'DISCORD_WEBHOOK_URL': 'https://discord.com/api/webhooks/123456789012345678/not-a-real-token'}
        with patch('cours_watch.discord_request', side_effect=RuntimeError('network')):
            with self.assertRaises(RuntimeError):
                notify(self.state, configuration, secrets)
        self.assertEqual(len(self.state.pending()), 1)
        with patch('cours_watch.discord_request', return_value={'id': 'mock-message'}) as send:
            self.assertEqual(notify(self.state, configuration, secrets)['events_delivered'], 1)
            payload = send.call_args.args[1]
            self.assertEqual(payload['allowed_mentions']['parse'], [])
            self.assertNotIn('@everyone', payload['content'])
        self.assertEqual(self.state.pending(), [])

    def test_foreign_cookie_export_is_rejected_before_browser(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)/'input.json'
            p.write_text(json.dumps([{'name': 'test', 'value': 'fake', 'domain': '.unrelated.example'}]))
            with self.assertRaises(ValueError):
                import_cookies(p, Path(d))

    def test_moodle_session_survives_restart_without_exporting_other_domains(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            save_moodle_cookies(root, [
                {'name': 'MoodleSession', 'value': 'fake-session', 'domain': 'curs.upb.ro', 'path': '/', 'expires': -1},
                {'name': 'unrelated', 'value': 'excluded', 'domain': 'unrelated.example', 'path': '/'}])
            restored = moodle_cookies(root)
            self.assertEqual(len(restored), 1)
            self.assertEqual(restored[0]['value'], 'fake-session')
            self.assertEqual(restored[0]['expires'], -1)
            save_moodle_cookies(root, [])
            self.assertEqual(moodle_cookies(root), restored)

    def test_forum_parent_link_does_not_create_a_false_change(self):
        def snapshot(url):
            canonical = canonical_forum_url(url)
            return Result('moodle:SOS', 'SOS', 'ok', [
                Item('forum:p2118', 'Intermediate presentation', 'Due 15 November', canonical + '#p2118')])
        self.state.ingest(snapshot('https://curs.upb.ro/2026/mod/forum/discuss.php?d=923'))
        self.assertEqual(self.state.ingest(snapshot('https://curs.upb.ro/2026/mod/forum/discuss.php?d=923&parent=2118#p2118')), [])

    def test_haddock_keeps_deadline_and_only_configured_mentions(self):
        event={'kind':'modified','course':'AITDM','source':'teams:AITDM',
               'title':'Deadline','body':'Due 15 November 2026, 14:00. @everyone',
               'url':'https://teams.cloud.microsoft/'}
        message=format_notification(event,'<@&123456789012345678>','haddock')
        self.assertIn('Tonnerre de Brest',message)
        self.assertIn('15 November 2026, 14:00',message)
        self.assertIn('<@&123456789012345678>',message)
        self.assertNotIn('@everyone',message)
        self.assertIn('<https://teams.cloud.microsoft/>',message)

    def test_first_file_fingerprint_is_quiet_but_real_content_change_alerts(self):
        def snapshot(fingerprint=None):
            items = [Item('module-1', 'Lecture PDF', '', 'https://curs.upb.ro/2026/mod/resource/view.php?id=1')]
            if fingerprint:
                items.append(Item('module-1:document', 'Lecture PDF', fingerprint, items[0].url))
            return Result('moodle:AAIT', 'AAIT', 'ok', items)
        self.state.ingest(snapshot())
        self.assertEqual(self.state.ingest(snapshot('SHA256 first')), [])
        self.assertEqual(self.state.ingest(snapshot('SHA256 changed'))[0]['kind'], 'modified')

    def test_existing_forum_snapshot_is_migrated_without_an_alert(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'state.db'
            previous = State(path)
            old = Item('forum:p2118', 'Presentation', 'Due 15 November',
                       'https://curs.upb.ro/2026/mod/forum/discuss.php?d=923&parent=2118#p2118')
            previous.ingest(Result('moodle:SOS', 'SOS', 'ok', [old]))
            previous.db.close()
            current = State(path)
            stable = Item(old.key, old.title, old.body, 'https://curs.upb.ro/2026/mod/forum/discuss.php?d=923#p2118')
            self.assertEqual(current.ingest(Result('moodle:SOS', 'SOS', 'ok', [stable])), [])
            current.db.close()

    def test_private_env_adds_bot_token_without_replacing_the_webhook(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / 'secrets.json'
            path.write_text(json.dumps({'DISCORD_WEBHOOK_URL':'fake-webhook'}))
            (root / '.env').write_text('DISCORD_BOT_TOKEN="fake-bot-token"\nIGNORED=value\n')
            values = load_discord_secrets(path)
            self.assertEqual(values['DISCORD_BOT_TOKEN'],'fake-bot-token')
            self.assertEqual(values['DISCORD_WEBHOOK_URL'],'fake-webhook')
            self.assertNotIn('IGNORED',values)


if __name__ == '__main__':
    unittest.main()
