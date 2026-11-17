"""Synthetic channel validation, including a strict no-mentions REST boundary."""
import json
import io
from contextlib import redirect_stderr
from pathlib import Path
import unittest
from unittest.mock import patch

from ops.validate_channel import NO_MENTIONS, main, safe_payload, validate_channel

GUILD = '1376984512046563519'
CHANNEL = '1570209088929800282'
APP = '1510767148006641866'
MESSAGE = '1572000000000000000'
CONFIG = {'discord': {'guild_id': GUILD, 'channel_id': CHANNEL, 'application_id': APP}}


class FakeDiscord:
    def __init__(self):
        self.calls = []
        self.receipt = {'id': MESSAGE, 'channel_id': CHANNEL, 'mentions': [],
                        'mention_roles': [], 'mention_everyone': False}

    def __call__(self, path, payload=None, method='GET'):
        self.calls.append((path, payload, method))
        if path == '/users/@me':
            return {'id': APP, 'bot': True}
        if path == '/channels/' + CHANNEL:
            return {'id': CHANNEL, 'guild_id': GUILD}
        if path.endswith('/commands'):
            return [{'name': name} for name in ('cours', 'actualiser', 'status', 'a-faire', 'settings')]
        return dict(self.receipt)


class ChannelValidationTests(unittest.IsolatedAsyncioTestCase):
    async def test_one_synthetic_message_and_real_callback_edits_never_allow_mentions(self):
        transport = FakeDiscord()
        result = await validate_channel(CONFIG, 'fake-token', send_test=True, transport=transport)
        writes = [(path, payload, method) for path, payload, method in transport.calls if method != 'GET']
        self.assertEqual([method for _, _, method in writes], ['POST', 'PATCH', 'PATCH', 'PATCH'])
        self.assertEqual([len(payload.get('embeds', [])) for _, payload, _ in writes], [3, 1, 3, 0])
        self.assertEqual(result['edited_embed_counts'], [1, 3])
        self.assertEqual(result['message_pages'], 2)
        self.assertEqual(result['interaction_mode'], 'callback_adapter_not_user_click')
        for path, payload, method in writes:
            self.assertEqual(payload['allowed_mentions'], NO_MENTIONS)
            self.assertNotIn('<@', json.dumps(payload))
            self.assertNotIn('@everyone', json.dumps(payload))
            self.assertTrue(path.startswith('/channels/' + CHANNEL + '/messages'))
            if method == 'PATCH':
                self.assertTrue(path.endswith('/' + MESSAGE))
        self.assertEqual(writes[-1][1]['components'], [])
        for row in writes[0][1]['components']:
            for component in row['components']:
                if component['style'] != 5:
                    self.assertTrue(component['disabled'])

    async def test_no_authorization_flag_causes_no_request(self):
        transport = FakeDiscord()
        with self.assertRaises(ValueError):
            await validate_channel(CONFIG, 'fake-token', transport=transport)
        self.assertEqual(transport.calls, [])

    async def test_wrong_channel_or_missing_command_prevents_public_post(self):
        for kind in ('scope', 'commands'):
            transport = FakeDiscord()
            def request(path, payload=None, method='GET'):
                value = transport(path, payload, method)
                if kind == 'scope' and path == '/channels/' + CHANNEL:
                    return {'id': CHANNEL, 'guild_id': '999999999999999999'}
                if kind == 'commands' and path.endswith('/commands'):
                    return [{'name': 'cours'}]
                return value
            with self.subTest(kind=kind), self.assertRaises(RuntimeError):
                await validate_channel(CONFIG, 'fake-token', send_test=True, transport=request)
            self.assertTrue(all(method == 'GET' for _, _, method in transport.calls))

    async def test_mention_in_receipt_fails_validation(self):
        transport = FakeDiscord()
        transport.receipt['mention_roles'] = ['1570209160744673411']
        with self.assertRaisesRegex(RuntimeError, 'mention'):
            await validate_channel(CONFIG, 'fake-token', send_test=True, transport=transport)
        self.assertEqual(transport.calls[-1][2], 'PATCH')
        self.assertEqual(transport.calls[-1][1]['components'], [])

    async def test_patch_failure_cleans_only_the_new_test_message(self):
        transport = FakeDiscord()
        count = 0
        def request(path, payload=None, method='GET'):
            nonlocal count
            if method == 'PATCH':
                count += 1
                if count == 1:
                    raise RuntimeError('synthetic failure')
            return transport(path, payload, method)
        with self.assertRaises(RuntimeError):
            await validate_channel(CONFIG, 'fake-token', send_test=True, transport=request)
        self.assertEqual(count, 2)
        final = transport.calls[-1]
        self.assertEqual(final[0], '/channels/' + CHANNEL + '/messages/' + MESSAGE)
        self.assertEqual(final[1]['components'], [])
        self.assertEqual(final[1]['allowed_mentions'], NO_MENTIONS)


class ChannelBoundaryTests(unittest.TestCase):
    def test_payload_scrubs_mentions_and_overrides_permissive_settings(self):
        original = {'content': '@everyone <@&1570209160744673411>',
                    'embeds': [{'description': '@here'}], 'allowed_mentions': {'parse': ['everyone']}}
        payload = safe_payload(original)
        self.assertEqual(payload['allowed_mentions'], NO_MENTIONS)
        self.assertNotIn('@everyone', payload['content'])
        self.assertNotIn('<@&', payload['content'])
        self.assertEqual(original['allowed_mentions'], {'parse': ['everyone']})

    def test_cli_without_send_flag_does_not_read_files(self):
        with patch.object(Path, 'read_text', side_effect=AssertionError('credential accessed')), redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as stopped:
                main([])
        self.assertEqual(stopped.exception.code, 2)


if __name__ == '__main__':
    unittest.main()
