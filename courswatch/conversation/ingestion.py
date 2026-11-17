"""Normalize already-authorized Discord events without participating in the chat."""
from datetime import datetime, timezone

from courswatch.conversation.brain import safe_text
from courswatch.conversation.contracts import MessageEnvelope, Scope, utc_iso


def envelope_from_message(message, ingestion_mode='live', now=None, author=None):
    from courswatch.media import normalize_media
    received = utc_iso(now or datetime.now(timezone.utc))
    created = getattr(message, 'created_at', None)
    edited = getattr(message, 'edited_at', None)
    scope = Scope(str(message.guild.id), str(message.channel.id))
    identity = str(message.id)
    reference = getattr(message, 'reference', None)
    reply_to = getattr(reference, 'message_id', None)
    author = author or message.author
    return MessageEnvelope(
        scope=scope, message_id=identity, author_id=str(author.id),
        display_name=safe_text(getattr(author, 'display_name', 'Camarade'), 80),
        text=safe_text(getattr(message, 'content', '') or '', 4000),
        created_at=utc_iso(created) if isinstance(created, datetime) else received,
        received_at=received, edited_at=utc_iso(edited) if edited else None,
        reply_to_message_id=str(reply_to) if reply_to is not None else None,
        mentioned_user_ids=tuple(str(user.id) for user in getattr(message, 'mentions', ())),
        source_url='https://discord.com/channels/' + scope.guild_id + '/' + scope.channel_id + '/' + identity,
        is_bot=bool(getattr(author, 'bot', False)),
        ingestion_mode=ingestion_mode, event_kind='edit' if edited else 'create',
        media=normalize_media(message))
