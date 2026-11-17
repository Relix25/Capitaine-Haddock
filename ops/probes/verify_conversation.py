"""Operator-only conversation check with synthetic messages and real metered models."""
import argparse
import asyncio
from contextlib import closing
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace

from courswatch.conversation.brain import LunaBrain
from courswatch.conversation.contracts import Scope
from courswatch.conversation.decision import JEV
from courswatch.conversation.models import effective_settings
from courswatch.storage.schema import migrate


class ProbeBrain(LunaBrain):
    def publish(self, *args, **kwargs):
        """The running bot owns the production status file. Keep its ledger active."""


def require(ok, label):
    if not ok:
        raise RuntimeError(label)


def fixture(root, scope):
    today = datetime.now(timezone.utc)
    courses = ['AAIT', 'SOS']
    tasks = []
    with closing(sqlite3.connect(root / 'state.db')) as db, db:
        migrate(db, 'state')
        for offset, course in enumerate(courses, start=1):
            source = 'moodle:' + course
            due = today + timedelta(days=offset + 7)
            url = 'https://example.org/haddock-probe/' + course
            item = {'key': 'fixture', 'title': 'Devoir fictif ' + course, 'url': url,
                    'body': 'Due: ' + due.strftime('%d %B %Y') + ', 06:00 PM'}
            payload = json.dumps(item)
            db.execute('INSERT INTO items VALUES(?,?,?,?)',
                       (source, 'fixture', hashlib.sha256(payload.encode()).hexdigest(), payload))
            db.execute('INSERT INTO sources VALUES(?,?,?,?)', (source, 'ok', 1, today.isoformat()))
            tasks.append({'id': course, 'course': course, 'title': item['title'], 'url': url,
                          'date_mode': 'moodle_due', 'reference': {'source': source, 'key': 'fixture'}})
    schedule = root / 'reminders.json'
    schedule.write_text(json.dumps({'timezone': 'Europe/Bucharest', 'tasks': tasks}), encoding='utf-8')
    return {'courses': [{'id': course} for course in courses], 'reminders_file': str(schedule),
            'discord': {'luna': {'knowledge': {'allowed_sources': {
                scope.key: ['moodle:' + course for course in courses]}}}}}


class SyntheticMessage:
    def __init__(self, identity, text, author, scope):
        self.id, self.content = identity, text
        self.guild = SimpleNamespace(id=scope.guild_id)
        self.channel = SimpleNamespace(id=scope.channel_id)
        self.author = SimpleNamespace(id=author, display_name='Membre fictif', bot=False)
        self.mentions = []
        self.created_at = datetime.now(timezone.utc)
        self.replies = []

    async def reply(self, content, *, allowed_mentions, mention_author):
        require(not any((mention_author, allowed_mentions.everyone, allowed_mentions.roles,
                         allowed_mentions.users, allowed_mentions.replied_user)), 'mention_policy')
        self.replies.append(content)
        return SimpleNamespace(id=self.id + 1000)


async def check(root, settings, brain):
    from courswatch.conversation.chat import LunaChat

    scope = Scope('1', '2')
    config = fixture(root, scope)
    status = {}
    client = SimpleNamespace(user=SimpleNamespace(id=99), voice_samples=(), voice_probability=0,
                             write_status=lambda **fields: status.update(fields))
    chat = LunaChat(client, root, settings, lambda: config, brain=brain)
    profile = brain.capture_profile()
    judge = profile.requested_model_id if profile.require_zdr else JEV.requested_model_id

    async def interact(label, identity, text, author, mentioned=False):
        status.clear()
        message = SyntheticMessage(identity, text, author, scope)
        chat.accept(message, mentioned=mentioned)
        if chat.task:
            await asyncio.wait_for(asyncio.shield(chat.task), timeout=90)
        for key in ('last_luna_error', 'last_luna_callback_error','last_luna_delivery_error'):
            if status.get(key):
                error = status[key]
                code = error.get('code') or error.get('error_type') if isinstance(error, dict) else 'delivery'
                raise RuntimeError(label + ':' + str(code))
        return message.replies

    try:
        # Exercise a negative participation decision before the first visible reply sets the cooldown.
        replies = await interact('between_people', 10, 'Camille, je t’ai laissé ton livre sur ta chaise. À demain, Léa.', 3)
        vote = status.get('last_participation', {})
        require(not replies and vote.get('respond') is False and vote.get('model') == judge
                and isinstance(vote.get('call_id'), int), 'between_people_vote')
        negative_call = vote['call_id']

        replies = await interact('greeting', 11, 'Bonjour mon capitaine !', 4, mentioned=True)
        timing = status.get('last_chat_timing', {})
        require(len(replies) == 1 and bool(replies[0].strip()) and isinstance(timing.get('call_id'), int)
                and timing.get('requested_model_id') == profile.requested_model_id
                and (timing.get('total_tokens') or 0) > 0, 'greeting_generation')
        greeting_call = timing['call_id']

        replies = await interact('aait', 12, 'Donne-moi les prochaines échéances pour AAIT ?', 4, mentioned=True)
        require(len(replies) == 1 and 'https://example.org/haddock-probe/AAIT' in replies[0]
                and 'https://example.org/haddock-probe/SOS' not in replies[0]
                and status.get('last_chat_timing', {}).get('call_id') is None, 'aait_local_sources')

        replies = await interact('sos', 13, 'Et SOS ?', 4)
        vote = status.get('last_participation', {})
        require(vote.get('respond') is True and vote.get('model') == judge
                and isinstance(vote.get('call_id'), int), 'sos_followup_vote')
        require(len(replies) == 1 and 'https://example.org/haddock-probe/SOS' in replies[0]
                and 'https://example.org/haddock-probe/AAIT' not in replies[0]
                and status.get('last_chat_timing', {}).get('call_id') is None, 'sos_local_sources')
        return {'status':'ok', 'discord_transport':'synthetic', 'conversation_model':profile.requested_model_id,
                'participation_model':judge, 'greeting_call':greeting_call, 'negative_vote_call':negative_call,
                'followup_vote_call':vote['call_id'], 'greeting':'generated', 'aait':'fixture_source',
                'sos':'fixture_source', 'between_people':'silent', 'mentions':0}
    finally:
        await chat.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live-models', action='store_true',
                        help='Run metered synthetic requests and record consumption in the existing quota ledger')
    parser.add_argument('--config', type=Path, default=Path('/etc/cours-watch/config.json'))
    parser.add_argument('--data', type=Path, default=Path('/var/lib/cours-watch'))
    args = parser.parse_args()
    if not args.live_models:
        parser.error('--live-models is required')
    require((args.data/'luna-usage.db').is_file(), 'production_ledger_missing')
    settings = effective_settings(args.data, json.loads(args.config.read_text())['discord']['luna'])
    brain = ProbeBrain(args.data, settings)
    require(bool(brain.key), 'model_key_missing')
    with tempfile.TemporaryDirectory(prefix='haddock-conversation-probe-') as temporary:
        result = asyncio.run(check(Path(temporary), settings, brain))
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
