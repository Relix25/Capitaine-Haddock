"""Attributed FIFO turns and dependency-specific optimistic publication checks."""
from dataclasses import dataclass, field, replace
import re
import hashlib
import time

from courswatch.conversation.contracts import ConversationSnapshot
from courswatch.conversation.participation import (
    CANCEL, CONTINUATION, CORRECTION, INCOMPLETE, THANKS, UNCERTAIN, substantive_answer,
)

OPEN_STATES = ('queued', 'generating')


class QueueFull(RuntimeError):
    pass


def _goal(text):
    text = re.sub(r'\bhaddock\b', '', text.casefold())
    return ' '.join(re.findall(r'\w+', text))


def _entities(text):
    # Conservative anchors prevent two course/project questions being coalesced.
    known = re.findall(r'\b(?:aait|aitdm|bdfa|sos)\b', text, re.I)
    others = re.findall(r'\b[A-Z][A-Z0-9_-]{2,}\b', text)
    return frozenset(entity.casefold() for entity in known + others)


def _compatible(first, second):
    left, right = _entities(first), _entities(second)
    return not left or not right or bool(left & right)


def _clarification(text):
    return ('?' not in text and not UNCERTAIN.search(text)
            and bool(CORRECTION.search(text) or CONTINUATION.search(text)))


@dataclass
class Turn:
    turn_id: str
    conversation_id: str
    scope: object
    trigger_ids: list[str]
    requester_ids: list[str]
    reply_target_id: str
    goal: str
    direct: bool
    opened_at: float
    ready_at: float
    expires_at: float
    revision: int = 1
    state: str = 'queued'
    attempts: int = 0
    message_ids: list[str] = field(default_factory=list)
    received_at: float = 0
    gateway_ms: float | None = None
    fragmenting: bool = False
    clarification_ids: list[str] = field(default_factory=list)
    followup_context: tuple[str, ...] = ()


class Coordinator:
    def __init__(self, *, max_pending=128, max_age=120, clock=time.monotonic):
        self.max_pending, self.max_age, self.clock = max_pending, max_age, clock
        self.messages = {}
        self.turns = {}
        self.sequence = 0
        self.bot_links = {}

    def _key(self, scope, identity):
        return (scope.key, str(identity))

    def active(self, scope, identity):
        message = self.messages.get(self._key(scope, identity))
        return message if message and not message.deleted else None

    def observe(self, message):
        key = self._key(message.scope, message.message_id)
        old = self.messages.get(key)
        if old is not None:
            if old.deleted:
                return False
            if message == old:
                return False
            if (message.edited_at or '') < (old.edited_at or ''):
                return False
            if message.revision <= old.revision:
                if message.text == old.text and message.media == old.media:
                    if (message.revision == old.revision and old.ingestion_mode == 'backfill'
                            and message.ingestion_mode == 'live' and message.event_kind == 'create'):
                        self.messages[key] = message
                        return True
                    return False
                message = replace(message, revision=old.revision + 1)
        self.messages[key] = message
        if old is not None:
            for turn in self.turns.values():
                if turn.scope != message.scope or message.message_id not in turn.message_ids or turn.state not in OPEN_STATES:
                    continue
                turn.revision += 1
                if message.deleted:
                    turn.state = 'deleted'
                elif message.message_id in turn.trigger_ids and CANCEL.search(message.text):
                    self._withdraw_requester(turn, message.author_id, 'cancelled')
                else:
                    self._update_goal(turn)
        self.prune()
        return True

    def _update_goal(self, turn):
        prompts = [self.active(turn.scope, identity) for identity in turn.trigger_ids]
        lines = [item.text or 'Décrire le média joint' for item in prompts if item]
        prompt = '\n'.join(lines)
        for identity in tuple(turn.clarification_ids):
            item = self.active(turn.scope, identity)
            if item and _clarification(item.text) and _compatible(prompt, item.text):
                lines.append('Précision liée : ' + item.text)
            else:
                turn.clarification_ids.remove(identity)
                turn.message_ids.remove(identity)
        turn.goal = '\n'.join(lines)

    def _same_goal(self, turn, message):
        if message.media or not message.text.strip():
            return False
        return all((item := self.active(turn.scope, identity)) and not item.media
                   and item.reply_to_message_id == message.reply_to_message_id
                   and _goal(item.text) == _goal(message.text) for identity in turn.trigger_ids)

    def _withdraw_requester(self, turn, author_id, state):
        if len(turn.requester_ids) == 1:
            turn.state = state
            return
        # Another user can still need an answer to the shared request.
        turn.requester_ids.remove(author_id)
        removed = {identity for identity in turn.message_ids
                   if (item := self.active(turn.scope, identity)) and item.author_id == author_id}
        turn.trigger_ids = [identity for identity in turn.trigger_ids if identity not in removed]
        turn.clarification_ids = [identity for identity in turn.clarification_ids if identity not in removed]
        turn.message_ids = [identity for identity in turn.message_ids if identity not in removed]
        if turn.reply_target_id in removed:
            turn.reply_target_id = turn.trigger_ids[0]
        turn.revision += 1
        self._update_goal(turn)

    def _linked_turn(self, message):
        reply = message.reply_to_message_id
        if not reply:
            return None
        linked = self.bot_links.get(self._key(message.scope, reply), reply)
        now = self.clock()
        return next((turn for turn in reversed(tuple(self.turns.values()))
                     if turn.scope == message.scope and linked in turn.message_ids
                     and turn.state in OPEN_STATES and now <= turn.expires_at), None)

    def _attach_trigger(self, turn, message, direct, now):
        same_author = message.author_id in turn.requester_ids
        turn.trigger_ids.append(message.message_id)
        turn.message_ids.append(message.message_id)
        if same_author:
            turn.reply_target_id = message.message_id
        else:
            turn.requester_ids.append(message.author_id)
        if re.search(r'[?!]\s*$', message.text):
            turn.fragmenting = False
        if turn.state == 'queued':
            prior_ready = now if direct and not turn.direct else turn.ready_at
            turn.ready_at = min(turn.opened_at + 1.5, max(prior_ready, now + .15))
        turn.direct = turn.direct or direct
        turn.revision += 1
        self._update_goal(turn)
        return turn

    def enqueue(self, message, direct, *, wait_ms=None, gateway_ms=None, allow_new=True, received_at=None):
        now = self.clock()
        received_at = now if received_at is None else received_at
        expires_at = received_at + self.max_age - max(0, gateway_ms or 0) / 1000
        if (message.ingestion_mode != 'live' or message.deleted or message.is_bot or now >= expires_at):
            return None
        candidates = [turn for turn in self.turns.values() if turn.scope == message.scope
                      and turn.state in OPEN_STATES and now <= turn.expires_at]
        linked = self._linked_turn(message)
        if CANCEL.search(message.text):
            if linked and message.author_id in linked.requester_ids:
                self._withdraw_requester(linked, message.author_id, 'cancelled')
            elif not message.reply_to_message_id:
                own = [turn for turn in candidates if message.author_id in turn.requester_ids
                       and _compatible(turn.goal, message.text)]
                if len(own) == 1:
                    self._withdraw_requester(own[0], message.author_id, 'cancelled')
            return None
        if THANKS.fullmatch(message.text.strip()):
            if linked and message.author_id in linked.requester_ids:
                self._withdraw_requester(linked, message.author_id, 'answered')
            return None

        if linked is not None:
            same_author = message.author_id in linked.requester_ids
            if _compatible(linked.goal, message.text):
                clarification = _clarification(message.text)
                exact = direct and self._same_goal(linked, message)
                fragment = same_author and (linked.fragmenting or clarification)
                if exact or fragment:
                    return self._attach_trigger(linked, message, direct, now)
                if not direct and not same_author:
                    if clarification:
                        linked.clarification_ids.append(message.message_id)
                        linked.message_ids.append(message.message_id)
                        linked.revision += 1
                        self._update_goal(linked)
                        return linked
                    if substantive_answer(message.text, linked.goal):
                        linked.state = 'answered'
            if not direct:
                # A short aside stays in the journal without invalidating the answer in progress.
                return None

        for turn in reversed(candidates):
            if turn is linked or turn.state != 'queued' or now - turn.opened_at > 1.5:
                continue
            same_author = message.author_id in turn.requester_ids
            exact = direct and self._same_goal(turn, message)
            fragment = (same_author and _compatible(turn.goal, message.text)
                        and (direct or not message.mentioned_user_ids)
                        and (turn.fragmenting or _clarification(message.text)))
            if exact or fragment:
                return self._attach_trigger(turn, message, direct, now)
        if not allow_new:
            return None
        if len(candidates) >= self.max_pending:
            raise QueueFull('conversation_queue_full')
        self.sequence += 1
        identity = 'turn_' + hashlib.sha256((message.scope.key+':'+message.message_id).encode()).hexdigest()[:24]
        delay = wait_ms / 1000 if wait_ms is not None else (.15 if direct else 4)
        turn = Turn(identity, identity, message.scope, [message.message_id], [message.author_id], message.message_id,
                    message.text or 'Décrire le média joint', direct, now, now + delay, expires_at,
                    message_ids=[message.message_id], received_at=received_at, gateway_ms=gateway_ms,
                    fragmenting=bool(INCOMPLETE.search(message.text)))
        self.turns[identity] = turn
        return turn

    def next(self):
        now = self.clock()
        for turn in self.turns.values():
            if turn.state == 'queued' and now > turn.expires_at:
                turn.state = 'expired'
        queued = [turn for turn in self.turns.values() if turn.state == 'queued']
        return min(queued, key=lambda turn: (not turn.direct, turn.opened_at)) if queued else None

    def freeze(self, turn):
        messages = [self.active(turn.scope, identity) for identity in turn.message_ids]
        messages = [message for message in messages if message is not None]
        prior = [self.active(turn.scope, identity) for identity in turn.followup_context]
        messages = [message for message in prior if message and not message.deleted and message not in messages] + messages
        # Include explicit ancestors, never unrelated messages from another topic.
        for message in tuple(messages):
            identity = message.reply_to_message_id
            for _ in range(8):
                ancestor = self.active(turn.scope, identity) if identity else None
                if ancestor is None or ancestor in messages:
                    break
                messages.insert(0, ancestor)
                identity = ancestor.reply_to_message_id
        return ConversationSnapshot(turn.turn_id, turn.conversation_id, turn.revision, turn.scope,
            tuple(messages[-30:]), tuple(turn.trigger_ids), tuple(turn.requester_ids), turn.reply_target_id,
            turn.goal, tuple((message.message_id, message.revision) for message in messages))

    def valid(self, snapshot):
        turn = self.turns.get(snapshot.turn_id)
        return (turn is not None and turn.state in OPEN_STATES and turn.revision == snapshot.revision
                and self.clock() <= turn.expires_at and all(
                    (message := self.active(snapshot.scope, identity)) is not None and message.revision == revision
                    for identity, revision in snapshot.dependencies))

    def retry(self, turn):
        if turn.state not in ('queued', 'generating') or turn.attempts >= 2 or self.clock() > turn.expires_at:
            if turn.state in ('queued', 'generating'):
                turn.state = 'abandoned'
            return False
        turn.state = 'queued'
        turn.ready_at = self.clock()
        return True

    def delete(self, scope, identity):
        message = self.messages.get(self._key(scope, identity))
        if message is not None:
            self.messages[self._key(scope, identity)] = replace(message, text='', media=(), deleted=True, revision=message.revision + 1)
        for turn in self.turns.values():
            if turn.scope == scope and str(identity) in turn.message_ids and turn.state in ('queued', 'generating'):
                turn.state = 'deleted'
                turn.revision += 1

    def finish(self, turn, state='completed', receipt=None):
        turn.state = state
        self.prune()
        if receipt is not None:
            self.bot_links[self._key(turn.scope, receipt)] = turn.reply_target_id

    def prune(self):
        active=[turn for turn in self.turns.values() if turn.state in ('queued','generating')]
        protected={(turn.scope.key,identity) for turn in active for identity in turn.message_ids}
        finished=[turn.turn_id for turn in self.turns.values() if turn.state not in ('queued','generating')]
        for identity in finished[:-self.max_pending]:
            del self.turns[identity]
        for key in tuple(self.messages)[:-max(512,self.max_pending*4)]:
            if key not in protected:
                del self.messages[key]
        for key in tuple(self.bot_links)[:-256]:
            del self.bot_links[key]

    @property
    def pending_direct(self):
        return any(turn.direct and turn.state == 'queued' for turn in self.turns.values())
