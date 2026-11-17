"""Initial/deferred responses retain privacy and avoid an unnecessary HTTP round trip."""
import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
import threading
import unittest
from unittest.mock import AsyncMock

from courswatch.discord_app.responses import prepare_private, send_private


def interaction():
    state = {'done': False}
    response = SimpleNamespace(is_done=lambda: state['done'])

    async def initial(*args, **kwargs):
        state['done'] = True
        return SimpleNamespace(resource=SimpleNamespace(id=123), message_id=123)

    async def defer(**kwargs):
        state['done'] = True

    response.send_message = AsyncMock(side_effect=initial)
    response.defer = AsyncMock(side_effect=defer)
    return SimpleNamespace(response=response, followup=SimpleNamespace(send=AsyncMock(return_value=SimpleNamespace(id=456))),
                           original_response=AsyncMock(return_value=SimpleNamespace(id=123)),
                           created_at=datetime.now(timezone.utc))


class ResponseTests(unittest.IsolatedAsyncioTestCase):
    async def test_ready_cache_uses_only_initial_response_and_never_defers(self):
        request = interaction()
        value, timing = await prepare_private(request, lambda: 'cached')
        await send_private(request, value, timing=timing)
        request.response.defer.assert_not_awaited()
        request.response.send_message.assert_awaited_once()
        request.followup.send.assert_not_awaited()
        self.assertTrue(request.response.send_message.await_args.kwargs['ephemeral'])
        self.assertFalse(request.response.send_message.await_args.kwargs['allowed_mentions'].roles)
        self.assertTrue(timing['initial_response'])
        self.assertFalse(timing['deferred'])

    async def test_slow_worker_is_acknowledged_before_waiting_for_its_result(self):
        request = interaction()
        released = threading.Event()
        self.addCleanup(released.set)
        original_defer = request.response.defer.side_effect

        async def defer(**kwargs):
            await original_defer(**kwargs)
            released.set()

        request.response.defer.side_effect = defer

        def worker():
            if not released.wait(timeout=3):
                raise AssertionError('Worker was awaited before acknowledgement')
            return 'slow result'

        value, timing = await prepare_private(request, worker, _budget=0)
        await send_private(request, value, timing=timing)
        request.response.defer.assert_awaited_once_with(ephemeral=True, thinking=True)
        request.response.send_message.assert_not_awaited()
        request.followup.send.assert_awaited_once()
        self.assertTrue(request.followup.send.await_args.kwargs['ephemeral'])
        self.assertTrue(timing['deferred'])

    async def test_existing_ack_is_not_repeated(self):
        request = interaction()
        await request.response.defer(ephemeral=True, thinking=True)
        value, timing = await prepare_private(request, lambda: 'cached')
        await send_private(request, value, timing=timing)
        request.response.defer.assert_awaited_once()
        request.followup.send.assert_awaited_once()

    async def test_initial_callback_id_binds_card_without_an_extra_get(self):
        request = interaction()
        request.response.send_message.return_value = SimpleNamespace(resource=None, message_id=123)
        request.response.send_message.side_effect = None
        result = await send_private(request, 'card', fetch_message=True)
        self.assertEqual(result.id, 123)
        request.original_response.assert_not_awaited()

    async def test_second_card_is_a_private_followup(self):
        request = interaction()
        await send_private(request, 'first', fetch_message=True)
        await send_private(request, 'second', fetch_message=True)
        request.response.send_message.assert_awaited_once()
        request.followup.send.assert_awaited_once_with('second', wait=True, ephemeral=True,
                                                     allowed_mentions=unittest.mock.ANY)

    async def test_failed_preparation_does_not_send_or_expose_the_exception(self):
        request = interaction()
        def fail():
            raise ValueError('private synthetic text')
        with self.assertRaises(ValueError):
            await prepare_private(request, fail)
        request.response.send_message.assert_not_awaited()
        request.followup.send.assert_not_awaited()


if __name__ == '__main__':
    unittest.main()
