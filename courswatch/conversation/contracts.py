"""Small immutable contracts shared by ingestion, coordination and retrieval.

Times are aware ISO-8601 UTC strings. IDs identify internal records, never pings.
Retrieved text, OCR and filenames remain untrusted data.
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json


def utc_iso(value=None):
    value = value or datetime.now(timezone.utc)
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if value.tzinfo is None:
        raise ValueError('An aware timestamp is required')
    return value.astimezone(timezone.utc).isoformat()


@dataclass(frozen=True)
class Scope:
    guild_id: str
    channel_id: str
    thread_id: str = ''

    def __post_init__(self):
        for name in ('guild_id', 'channel_id', 'thread_id'):
            object.__setattr__(self, name, str(getattr(self, name)))
        if not self.guild_id or not self.channel_id:
            raise ValueError('A guild and channel scope are required')

    @property
    def key(self):
        return json.dumps([self.guild_id, self.channel_id, self.thread_id], separators=(',', ':'))


AccessScope = Scope


@dataclass(frozen=True)
class MediaDescriptor:
    media_id: str
    kind: str
    url: str
    source_kind: str = 'discord_attachment'
    position: int = 0
    content_type: str = ''
    filename: str = ''
    size_bytes: int | None = None
    emoji_id: str | None = None
    animated: bool = False


@dataclass(frozen=True)
class MessageEnvelope:
    scope: Scope
    message_id: str
    author_id: str
    display_name: str
    text: str
    created_at: str
    received_at: str = ''
    revision: int = 1
    edited_at: str | None = None
    reply_to_message_id: str | None = None
    mentioned_user_ids: tuple[str, ...] = ()
    source_url: str = ''
    is_bot: bool = False
    deleted: bool = False
    event_kind: str = 'create'
    ingestion_mode: str = 'live'
    media: tuple[MediaDescriptor, ...] = ()

    @property
    def content(self):
        return self.text

    @property
    def reply_to_id(self):
        return self.reply_to_message_id


@dataclass(frozen=True)
class ConversationSnapshot:
    turn_id: str
    conversation_id: str
    revision: int
    scope: Scope
    messages: tuple[MessageEnvelope, ...]
    trigger_message_ids: tuple[str, ...]
    requester_ids: tuple[str, ...]
    reply_target_id: str
    goal: str
    dependencies: tuple[tuple[str, int], ...]

    def as_context(self):
        return [dict(message_id=m.message_id, author_id=m.author_id,
                     speaker=m.display_name, text=m.text, is_bot=m.is_bot,
                     created_at=m.created_at, revision=m.revision,
                     reply_to_message_id=m.reply_to_message_id, source_url=m.source_url)
                for m in self.messages if not m.deleted]


@dataclass(frozen=True)
class ParticipationDecision:
    decision: str
    reason_code: str
    response_goal: str = ''
    recheck_after_ms: int | None = None

    def __post_init__(self):
        if self.decision not in ('respond', 'wait', 'silence'):
            raise ValueError('Invalid participation decision')


@dataclass(frozen=True)
class RetrievalRequest:
    scope: Scope
    query: str
    intent: str = 'auto'
    after: str | None = None
    before: str | None = None
    author_id: str | None = None
    budget_chars: int = 7000


@dataclass(frozen=True)
class Evidence:
    id: str
    kind: str
    scope: Scope
    source_id: str
    source_url: str
    title: str
    excerpt: str
    version: str
    observed_at: str
    published_at: str | None = None
    author_id: str | None = None
    date_value: str | None = None
    date_precision: str | None = None
    status: str = 'current'


@dataclass(frozen=True)
class EvidenceBundle:
    evidence: tuple[Evidence, ...] = ()
    clarification: str = ''
    coverage: str = ''
    gaps: tuple[str, ...] = ()
    dependencies: tuple[Evidence, ...] = ()


@dataclass(frozen=True)
class ModelCapabilities:
    image_input: str = 'unknown'
    multi_image: str = 'unknown'
    structured_output: str = 'unknown'
    api_image_input: str = 'unknown'
    api_structured_output: str = 'unknown'
    verified_real: bool = False
    evidence: str = ''
    formats: tuple[str, ...] = ('image/png', 'image/jpeg', 'image/webp')
    max_payload_bytes: int = 8 * 1024 * 1024


@dataclass(frozen=True)
class ModelProfile:
    profile_id: str
    provider: str
    endpoint: str
    requested_model_id: str
    require_zdr: bool
    config_version: str = '1'
    capabilities: ModelCapabilities = field(default_factory=ModelCapabilities)


@dataclass(frozen=True)
class PreparedReply:
    text: str
    snapshot: ConversationSnapshot
    citation_ids: tuple[str, ...] = ()
    evidence: EvidenceBundle | None = None
    dedupe_key: str = ''
