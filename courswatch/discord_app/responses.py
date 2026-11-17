"""Use one private response for ready data, with a bounded defer fallback."""
import asyncio
from datetime import datetime, timezone
from time import perf_counter
from types import SimpleNamespace

import discord

FIRST_RESPONSE_BUDGET = 0.5


def response_done(interaction):
    return interaction.response.is_done()


async def prepare_private(interaction, function, *args, _budget=FIRST_RESPONSE_BUDGET, **kwargs):
    started = perf_counter()
    timing = {'_started': started, 'deferred': response_done(interaction)}
    created = getattr(interaction, 'created_at', None)
    if not timing['deferred'] and isinstance(created, datetime) and created.tzinfo is not None:
        timing['gateway_ms'] = round(max(0, (datetime.now(timezone.utc) - created).total_seconds() * 1000), 2)
    task = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
    try:
        done, _ = await asyncio.wait({task}, timeout=_budget)
        if not done and not response_done(interaction):
            before_ack = perf_counter()
            await interaction.response.defer(ephemeral=True, thinking=True)
            timing.update(deferred=True, ack_ms=round((perf_counter() - before_ack) * 1000, 2))
        value = await task
    except BaseException:
        task.cancel()
        raise
    timing['prepare_ms'] = round((perf_counter() - started) * 1000, 2)
    return value, timing


async def send_private(interaction, *args, timing=None, fetch_message=False, **payload):
    # These constraints apply equally to the initial response and later messages.
    payload['ephemeral'] = True
    payload['allowed_mentions'] = discord.AllowedMentions.none()
    before_send = perf_counter()
    initial = not response_done(interaction)
    if initial:
        callback = await interaction.response.send_message(*args, **payload)
        message = getattr(callback, 'resource', None)
        if message is None and getattr(callback, 'message_id', None) is not None:
            message = SimpleNamespace(id=callback.message_id)
    else:
        message = await interaction.followup.send(*args, wait=fetch_message, **payload)
    sent = perf_counter()
    if timing is not None:
        timing.update(initial_response=initial, send_ms=round((sent - before_send) * 1000, 2))
        timing['total_ms'] = round((sent - timing.get('_started', before_send)) * 1000, 2)
    # discord.py 2.7 normally supplies the message ID in the callback, so no
    # extra GET is required to bind the first private card to its requester.
    if fetch_message and message is None:
        message = await interaction.original_response()
    return message
