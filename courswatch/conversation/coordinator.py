"""Attributed FIFO turns and dependency-specific optimistic publication checks."""
from dataclasses import dataclass, field, replace
import re
import hashlib
import time

from courswatch.conversation.contracts import ConversationSnapshot
from courswatch.conversation.participation import CANCEL, CONTINUATION, CORRECTION, INCOMPLETE, THANKS, substantive_answer


class QueueFull(RuntimeError):
    pass


def _goal(text):
    text = re.sub(r'\bhaddock\b', '', text.casefold())
    return ' '.join(re.findall(r'\w+', text))


def _entities(text):
    # Conservative anchors prevent two course/project questions being coalesced.
    return frozenset(re.findall(r'\b(?:aait|aitdm|bdfa|sos|[A-Z][A-Z0-9_-]{2,})\b', text))


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
                    return False
                message = replace(message, revision=old.revision + 1)
        self.messages[key] = message
        if old is not None:
            for turn in self.turns.values():
                if turn.scope == message.scope and message.message_id in turn.message_ids and turn.state in ('queued', 'generating'):
                    turn.revision += 1
                    if message.deleted:
                        turn.state = 'deleted'
                    elif CANCEL.search(message.text):
                        turn.state = 'cancelled'
                    elif message.message_id in turn.trigger_ids:
                        turn.goal = '\n'.join(self.active(turn.scope, identity).text for identity in turn.trigger_ids if self.active(turn.scope, identity))
        self.prune()
        return True

    def _linked_turn(self, message):
        reply = message.reply_to_message_id
        if not reply:
            return None
        linked = self.bot_links.get(self._key(message.scope, reply), reply)
        return next((turn for turn in reversed(tuple(self.turns.values()))
                     if turn.scope == message.scope and linked in turn.message_ids
                     and turn.state in ('queued', 'generating')), None)

    def can_attach_fragment(self,message):
        return any(turn.state=='queued' and turn.scope==message.scope and turn.fragmenting
                   and message.author_id in turn.requester_ids and self.clock()-turn.opened_at<=1.5
                   and not (_entities(turn.goal) and _entities(message.text)
                            and _entities(turn.goal)!=_entities(message.text))
                   for turn in self.turns.values())

    def enqueue(self, message, direct, *, wait_ms=None, gateway_ms=None):
        now = self.clock()
        candidates = [turn for turn in self.turns.values() if turn.scope == message.scope and turn.state in ('queued', 'generating')]
        linked = self._linked_turn(message)
        if CANCEL.search(message.text):
            for turn in candidates:
                if turn is linked or (not message.reply_to_message_id and message.author_id in turn.requester_ids
                    and (len([item for item in candidates if message.author_id in item.requester_ids]) == 1
                         or bool(_entities(message.text) & _entities(turn.goal)))):
                    turn.state = 'cancelled'
            return None
        if linked is not None:
            if THANKS.fullmatch(message.text.strip()):
                linked.state = 'answered'
                return None
            if message.message_id not in linked.message_ids:
                linked.message_ids.append(message.message_id)
                linked.revision += 1
            # A substantive answer from someone else resolves the pending question;
            # corrections and new explicit solicitations instead rebuild its snapshot.
            if (not direct and message.author_id not in linked.requester_ids
                    and not CORRECTION.search(message.text) and substantive_answer(message.text,linked.goal)):
                linked.state = 'answered'
                return None
            if direct and message.author_id not in linked.requester_ids:
                linked.requester_ids.append(message.author_id)
            if message.author_id in linked.requester_ids:
                linked.reply_target_id = message.message_id
                if message.message_id not in linked.trigger_ids:
                    linked.trigger_ids.append(message.message_id)
                linked.goal += '\n' + message.text
            return linked
        for turn in reversed(candidates):
            if turn.state != 'queued' or now - turn.opened_at > 1.5:
                continue
            same_author = message.author_id in turn.requester_ids
            compatible = not (_entities(turn.goal) and _entities(message.text) and _entities(turn.goal) != _entities(message.text))
            exact = direct and _goal(turn.goal) == _goal(message.text)
            fragment = same_author and compatible and (CONTINUATION.search(message.text) or INCOMPLETE.search(turn.goal) or turn.fragmenting)
            if exact or fragment:
                turn.trigger_ids.append(message.message_id)
                turn.message_ids.append(message.message_id)
                if message.author_id not in turn.requester_ids:
                    turn.requester_ids.append(message.author_id)
                turn.goal += '\n' + message.text
                if re.search(r'[?!]\s*$',message.text):
                    turn.fragmenting=False
                turn.reply_target_id = message.message_id if same_author else turn.reply_target_id
                turn.direct = turn.direct or direct
                turn.revision += 1
                turn.ready_at = min(turn.opened_at + 1.5, max(turn.ready_at, now + .15))
                return turn
        if len(candidates) >= self.max_pending:
            raise QueueFull('conversation_queue_full')
        self.sequence += 1
        identity = 'turn_' + hashlib.sha256((message.scope.key+':'+message.message_id).encode()).hexdigest()[:24]
        delay = wait_ms / 1000 if wait_ms is not None else (.15 if direct else 4)
        turn = Turn(identity, identity, message.scope, [message.message_id], [message.author_id], message.message_id,
                    message.text or 'Décrire le média joint', direct, now, now + delay, now + self.max_age,
                    message_ids=[message.message_id], received_at=now, gateway_ms=gateway_ms,
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
        turn = self.turns[snapshot.turn_id]
        return (turn.state in ('queued', 'generating') and turn.revision == snapshot.revision
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
