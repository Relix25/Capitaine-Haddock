"""Durable scoped messages, searchable active revisions and ingestion coverage.

All SQL reads carry a scope. Deletions remove text, descriptions and old version
payloads, but preserve a metadata tombstone so delayed events cannot revive them.
Connections belong to one operation; none are shared across worker threads.
"""
from __future__ import annotations

from contextlib import closing, contextmanager
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from typing import Callable

from courswatch.conversation.contracts import (ConversationSnapshot, MediaDescriptor,
                                               MessageEnvelope, Scope, utc_iso)

_SCHEMA_VERSION = 2


def _moment(value):
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError('An aware timestamp is required')
    return value.astimezone(timezone.utc)


def _payload(envelope):
    return json.dumps(asdict(envelope), ensure_ascii=False, sort_keys=True)


def _envelope(payload):
    value = json.loads(payload)
    value['scope'] = Scope(**value['scope'])
    value['media'] = tuple(MediaDescriptor(**row) for row in value.get('media', ()))
    value['mentioned_user_ids'] = tuple(value.get('mentioned_user_ids', ()))
    return MessageEnvelope(**value)


def _semantic(envelope):
    from courswatch.media import media_identity

    value = asdict(envelope)
    for key in ('received_at', 'revision', 'event_kind', 'ingestion_mode'):
        value.pop(key, None)
    value['media'] = [media_identity(item) for item in envelope.media]
    return value


def _search_text(observation):
    # Whitelist observational fields; model instructions/provenance are not facts.
    if observation.get('status') != 'ready':
        return ''
    values = [observation.get(key, '') for key in ('description', 'ocr', 'animation')]
    return ' '.join(value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
                    for value in values if value)


class Journal:
    def __init__(self, path, retention_days=90, now: Callable | None = None):
        if not isinstance(retention_days, int) or retention_days < 7:
            raise ValueError('Conversation retention must cover at least seven days')
        self.path = Path(path)
        self.retention_days = retention_days
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._migrate()
        self.path.chmod(0o600)
        self.purge()

    def _now(self):
        return _moment(self.now())

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        try:
            yield db
        finally:
            db.close()

    def _migrate(self):
        with self._db() as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('BEGIN IMMEDIATE')
            try:
                db.execute('CREATE TABLE IF NOT EXISTS schema_migrations('
                           'component TEXT,version INTEGER,applied_utc TEXT,'
                           'PRIMARY KEY(component,version))')
                applied = {row[0] for row in db.execute(
                    "SELECT version FROM schema_migrations WHERE component='conversation'")}
                if applied - set(range(1, _SCHEMA_VERSION + 1)):
                    raise RuntimeError('Conversation schema is newer than this application')
                if 1 not in applied:
                    statements = (
                        '''CREATE TABLE messages(scope TEXT NOT NULL,message_id TEXT NOT NULL,
                        revision INTEGER NOT NULL,author_id TEXT NOT NULL,created_at TEXT NOT NULL,
                        state_at TEXT NOT NULL,observed_at TEXT NOT NULL,deleted INTEGER NOT NULL,
                        payload TEXT NOT NULL,PRIMARY KEY(scope,message_id))''',
                        '''CREATE TABLE message_versions(scope TEXT NOT NULL,message_id TEXT NOT NULL,
                        revision INTEGER NOT NULL,observed_at TEXT NOT NULL,payload TEXT NOT NULL,
                        PRIMARY KEY(scope,message_id,revision))''',
                        '''CREATE VIRTUAL TABLE message_fts USING fts5(
                        scope UNINDEXED,message_id UNINDEXED,revision UNINDEXED,text,
                        tokenize='unicode61 remove_diacritics 2')''',
                        '''CREATE TABLE media_observations(scope TEXT NOT NULL,message_id TEXT NOT NULL,
                        revision INTEGER NOT NULL,media_id TEXT NOT NULL,payload TEXT NOT NULL,
                        PRIMARY KEY(scope,message_id,revision,media_id))''',
                        '''CREATE TABLE ingestion_cursors(scope TEXT PRIMARY KEY,cursor TEXT,
                        first_at TEXT,last_at TEXT,last_success TEXT,gaps TEXT NOT NULL DEFAULT '[]',
                        complete INTEGER NOT NULL DEFAULT 0)''',
                        '''CREATE TABLE outbound_responses(dedupe_key TEXT PRIMARY KEY,scope TEXT NOT NULL,
                        turn_id TEXT NOT NULL,snapshot_revision INTEGER NOT NULL,dependencies TEXT NOT NULL,
                        status TEXT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,
                        message_id TEXT)''',
                        'CREATE INDEX messages_period ON messages(scope,created_at)',
                        'CREATE INDEX messages_author ON messages(scope,author_id,created_at)',
                    )
                    for statement in statements:
                        db.execute(statement)
                    db.execute('INSERT INTO schema_migrations VALUES(?,?,?)',
                               ('conversation', 1, utc_iso(self._now())))
                if 2 not in applied:
                    db.execute('''CREATE TABLE coverage_windows(scope TEXT NOT NULL,start_at TEXT NOT NULL,
                               end_at TEXT NOT NULL,complete INTEGER NOT NULL,reason TEXT NOT NULL,
                               PRIMARY KEY(scope,start_at,end_at,complete))''')
                    # Version 1's convex min/max could conceal disjoint scans.
                    # Keep its messages and cursor, but require a new scoped scan.
                    for row in db.execute('SELECT scope,first_at,last_at FROM ingestion_cursors').fetchall():
                        if row['first_at'] and row['last_at']:
                            db.execute('INSERT INTO coverage_windows VALUES(?,?,?,?,?)',
                                       (row['scope'], row['first_at'], row['last_at'], 0, 'legacy_coverage_unverified'))
                    db.execute('INSERT INTO schema_migrations VALUES(?,?,?)',
                               ('conversation', 2, utc_iso(self._now())))
                db.commit()
            except BaseException:
                db.rollback()
                raise

    def _cutoff(self):
        return utc_iso(self._now() - timedelta(days=self.retention_days))

    def _index(self, db, scope, message_id):
        db.execute('DELETE FROM message_fts WHERE scope=? AND message_id=?', (scope, message_id))
        row = db.execute('SELECT payload,revision,deleted FROM messages WHERE scope=? AND message_id=?',
                         (scope, message_id)).fetchone()
        if row is None or row['deleted']:
            return
        envelope = _envelope(row['payload'])
        observed = [_search_text(json.loads(item[0])) for item in db.execute(
            'SELECT payload FROM media_observations WHERE scope=? AND message_id=? AND revision=?',
            (scope, message_id, row['revision']))]
        db.execute('INSERT INTO message_fts VALUES(?,?,?,?)',
                   (scope, message_id, row['revision'], '\n'.join([envelope.text, *observed])))

    def ingest(self, envelope: MessageEnvelope, backfill=False):
        """Persist a complete event. Duplicate/stale events return None.

        A backfill event cannot override a later edit, even if delivered last.
        Partial gateway updates must be merged with ``active`` by the adapter.
        """
        if not isinstance(envelope, MessageEnvelope):
            raise TypeError('MessageEnvelope required')
        if envelope.deleted:
            self.delete(envelope.scope, envelope.message_id, envelope.edited_at or envelope.received_at or None)
            return None
        created = utc_iso(envelope.created_at)
        if created < self._cutoff():
            return None
        state_at = utc_iso(envelope.edited_at or envelope.created_at)
        observed = utc_iso(self._now())
        envelope = replace(envelope, message_id=str(envelope.message_id), author_id=str(envelope.author_id),
                           created_at=created, edited_at=utc_iso(envelope.edited_at) if envelope.edited_at else None,
                           received_at=envelope.received_at or observed,
                           ingestion_mode='backfill' if backfill else envelope.ingestion_mode)
        scope, identifier = envelope.scope.key, envelope.message_id
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM messages WHERE scope=? AND message_id=?',
                             (scope, identifier)).fetchone()
            if row:
                if row['deleted'] or state_at < row['state_at']:
                    return None
                old = _envelope(row['payload'])
                if _semantic(old) == _semantic(envelope):
                    # A silent history read may win the race with the Gateway.
                    # Admit the first live create exactly once without invalidating
                    # evidence, media observations, or the content revision.
                    if (old.ingestion_mode == 'backfill' and envelope.ingestion_mode == 'live'
                            and envelope.event_kind == 'create' and not old.is_bot):
                        live_seen = db.execute(
                            """SELECT 1 FROM message_versions WHERE scope=? AND message_id=?
                               AND json_extract(payload,'$.ingestion_mode')='live'
                               AND json_extract(payload,'$.event_kind')='create' LIMIT 1""",
                            (scope, identifier)).fetchone()
                        if live_seen is None:
                            promoted = replace(envelope, revision=old.revision)
                            payload = _payload(promoted)
                            db.execute('UPDATE messages SET payload=? WHERE scope=? AND message_id=?',
                                       (payload, scope, identifier))
                            db.execute('UPDATE message_versions SET payload=? WHERE scope=? AND message_id=? AND revision=?',
                                       (payload, scope, identifier, old.revision))
                            db.commit()
                            return promoted
                    return None
                if state_at == row['state_at']:
                    if envelope.event_kind != 'rest' or utc_iso(envelope.received_at) < row['observed_at']:
                        return None
                if old.edited_at and not envelope.edited_at:
                    return None
                if old.author_id != envelope.author_id:
                    raise ValueError('A message author cannot change')
                envelope = replace(envelope, revision=row['revision'] + 1)
            else:
                envelope = replace(envelope, revision=1)
            payload = _payload(envelope)
            db.execute('INSERT OR REPLACE INTO messages VALUES(?,?,?,?,?,?,?,?,?)',
                       (scope, identifier, envelope.revision, envelope.author_id, created,
                        state_at, observed, 0, payload))
            db.execute('INSERT INTO message_versions VALUES(?,?,?,?,?)',
                       (scope, identifier, envelope.revision, observed, payload))
            # A derived description always belongs to one exact message revision.
            db.execute('DELETE FROM media_observations WHERE scope=? AND message_id=?', (scope, identifier))
            self._index(db, scope, identifier)
            db.commit()
        return envelope

    def active(self, scope: Scope, message_id):
        with self._db() as db:
            row = db.execute('SELECT payload FROM messages WHERE scope=? AND message_id=? '
                             'AND deleted=0 AND created_at>=?',
                             (scope.key, str(message_id), self._cutoff())).fetchone()
        return _envelope(row[0]) if row else None

    get = active

    def recent(self, scope: Scope, limit=30):
        limit = max(0, min(int(limit), 200))
        with self._db() as db:
            rows = db.execute('SELECT payload FROM messages WHERE scope=? AND deleted=0 AND created_at>=? '
                              'ORDER BY created_at DESC,message_id DESC LIMIT ?',
                              (scope.key, self._cutoff(), limit)).fetchall()
        return tuple(_envelope(row[0]) for row in reversed(rows))

    def authors(self, scope):
        with self._db() as db:
            rows = db.execute('SELECT author_id,payload FROM messages WHERE scope=? AND deleted=0 '
                              'AND created_at>=? ORDER BY created_at DESC',
                              (scope.key, self._cutoff())).fetchall()
        seen, result = set(), []
        for row in rows:
            if row['author_id'] not in seen:
                seen.add(row['author_id'])
                result.append((row['author_id'], _envelope(row['payload']).display_name))
        return tuple(result)

    def search(self, scope: Scope, query, after=None, before=None, author_id=None, limit=8, media_only=False):
        terms = re.findall(r'[^\W_]+', str(query), re.UNICODE)[:24]
        if not terms and not (after or before or author_id):
            return ()
        match = ' OR '.join('"' + term.replace('"', '""') + '"' for term in terms)
        where = ['f.scope=?', 'm.deleted=0', 'm.created_at>=?']
        parameters = [scope.key, self._cutoff()]
        if terms:
            where.append('message_fts MATCH ?'); parameters.append(match)
        if after is not None:
            where.append('m.created_at>=?'); parameters.append(utc_iso(after))
        if before is not None:
            where.append('m.created_at<?'); parameters.append(utc_iso(before))
        if author_id is not None:
            where.append('m.author_id=?'); parameters.append(str(author_id))
        if media_only:
            where.append('EXISTS (SELECT 1 FROM media_observations o WHERE o.scope=m.scope '
                         'AND o.message_id=m.message_id AND o.revision=m.revision)')
        cap = max(0, min(int(limit), 50))
        if not media_only:
            parameters.append(cap)
        with self._db() as db:
            rows = db.execute('SELECT m.payload FROM message_fts f JOIN messages m '
                              'ON m.scope=f.scope AND m.message_id=f.message_id AND m.revision=f.revision '
                              'WHERE ' + ' AND '.join(where) +
                              (' ORDER BY bm25(message_fts),m.created_at DESC' if terms else
                               ' ORDER BY m.created_at DESC') + ('' if media_only else ' LIMIT ?'), parameters)
            result = []
            for row in rows:
                if len(result) >= cap:
                    break
                message = _envelope(row[0])
                if media_only:
                    observations = db.execute('SELECT payload FROM media_observations WHERE scope=? '
                                              'AND message_id=? AND revision=?',
                                              (scope.key, message.message_id, message.revision))
                    if not any(json.loads(observation[0]).get('status') == 'ready' for observation in observations):
                        continue
                result.append(message)
        return tuple(result)

    def _delete_record(self, db, scope, identifier, observed):
        row = db.execute('SELECT payload,revision,deleted FROM messages WHERE scope=? AND message_id=?',
                         (scope.key, identifier)).fetchone()
        if row and row['deleted']:
            return False
        old = _envelope(row['payload']) if row else MessageEnvelope(scope, identifier, '', '', '', observed)
        tombstone = replace(old, text='', media=(), deleted=True,
                            mentioned_user_ids=(), display_name='', event_kind='delete',
                            revision=row['revision'] + 1 if row else 1, edited_at=observed)
        db.execute('INSERT OR REPLACE INTO messages VALUES(?,?,?,?,?,?,?,?,?)',
                   (scope.key, identifier, tombstone.revision, old.author_id, old.created_at,
                    observed, utc_iso(self._now()), 1, _payload(tombstone)))
        for table in ('message_versions', 'media_observations', 'message_fts'):
            db.execute('DELETE FROM ' + table + ' WHERE scope=? AND message_id=?', (scope.key, identifier))
        return True

    def delete(self, scope: Scope, message_id, at=None):
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            changed = self._delete_record(db, scope, str(message_id), utc_iso(at or self._now()))
            db.commit()
            return changed

    def save_observation(self, scope: Scope, message_id, revision, media_id, observation: dict):
        if not isinstance(observation, dict):
            raise TypeError('Observation must be a dictionary')
        identifier = str(message_id)
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT payload FROM messages WHERE scope=? AND message_id=? '
                             'AND revision=? AND deleted=0 AND created_at>=?',
                             (scope.key, identifier, revision, self._cutoff())).fetchone()
            if not row:
                return False
            envelope = _envelope(row[0])
            if str(media_id) not in {str(media.media_id) for media in envelope.media}:
                return False
            db.execute('INSERT OR REPLACE INTO media_observations VALUES(?,?,?,?,?)',
                       (scope.key, identifier, revision, str(media_id),
                        json.dumps(observation, ensure_ascii=False, sort_keys=True)))
            self._index(db, scope.key, identifier)
            db.commit()
        return True

    def observations(self, scope: Scope, message_id=None, media_id=None):
        where = ['o.scope=?', 'm.deleted=0', 'm.created_at>=?']
        parameters = [scope.key, self._cutoff()]
        if message_id is not None:
            where.append('o.message_id=?'); parameters.append(str(message_id))
        if media_id is not None:
            where.append('o.media_id=?'); parameters.append(str(media_id))
        with self._db() as db:
            rows = db.execute('SELECT o.*,m.payload AS message_payload FROM media_observations o JOIN messages m '
                              'ON m.scope=o.scope AND m.message_id=o.message_id AND m.revision=o.revision '
                              'WHERE ' + ' AND '.join(where), parameters).fetchall()
        result = []
        for row in rows:
            message = _envelope(row['message_payload'])
            result.append({'scope': scope, 'message_id': row['message_id'], 'revision': row['revision'],
                           'media_id': row['media_id'], 'author_id': message.author_id,
                           'display_name': message.display_name, 'created_at': message.created_at,
                           'source_url': message.source_url, 'observation': json.loads(row['payload'])})
        return tuple(result)

    def _record_window(self, db, scope_key, start, end, complete, reason=''):
        first, last = utc_iso(start), utc_iso(end)
        if first > last:
            raise ValueError('Coverage start must precede end')
        if complete:
            overlaps = db.execute('SELECT start_at,end_at FROM coverage_windows WHERE scope=? AND complete=1 '
                                  'AND start_at<=? AND end_at>=?', (scope_key, last, first)).fetchall()
            for row in overlaps:
                first, last = min(first, row['start_at']), max(last, row['end_at'])
                db.execute('DELETE FROM coverage_windows WHERE scope=? AND complete=1 AND start_at=? AND end_at=?',
                           (scope_key, row['start_at'], row['end_at']))
        db.execute('INSERT OR REPLACE INTO coverage_windows VALUES(?,?,?,?,?)',
                   (scope_key, first, last, int(complete), '' if complete else reason or 'incomplete_scan'))

    def checkpoint(self, scope: Scope, cursor=None, *, first_at=None, last_at=None,
                   gaps=None, complete=False):
        """Store pagination progress. Bounds alone never certify a full scan.

        Call ``record_coverage`` for requested and actually scanned windows.
        ``complete=True`` with both bounds is a convenience for a confirmed page.
        """
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            old = db.execute('SELECT * FROM ingestion_cursors WHERE scope=?', (scope.key,)).fetchone()
            old = dict(old) if old else {}
            first = utc_iso(first_at) if first_at else old.get('first_at')
            last = utc_iso(last_at) if last_at else old.get('last_at')
            if old.get('first_at') and first:
                first = min(first, old['first_at'])
            if old.get('last_at') and last:
                last = max(last, old['last_at'])
            old_gaps = json.loads(old.get('gaps', '[]'))
            for gap in gaps or ():
                if isinstance(gap, dict) and gap.get('start') and gap.get('end'):
                    self._record_window(db, scope.key, gap['start'], gap['end'], False, gap.get('reason', ''))
                elif gap not in old_gaps:
                    old_gaps.append(gap)
            if complete and first_at and last_at:
                self._record_window(db, scope.key, first_at, last_at, True)
            db.execute('INSERT OR REPLACE INTO ingestion_cursors VALUES(?,?,?,?,?,?,?)',
                       (scope.key, str(cursor) if cursor is not None else old.get('cursor'),
                        first, last, utc_iso(self._now()), json.dumps(old_gaps, ensure_ascii=False), 0))
            db.commit()
        return self.coverage(scope)

    def record_coverage(self, scope, start, end, *, complete=False, reason=''):
        """Record requested or confirmed bounds without erasing other gaps.

        A partial requested window followed by its confirmed prefix retains the
        unscanned suffix. Disjoint confirmed windows retain the gap between them.
        """
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            self._record_window(db, scope.key, start, end, complete, reason)
            db.execute('INSERT OR IGNORE INTO ingestion_cursors(scope,gaps) VALUES(?,?)', (scope.key, '[]'))
            db.execute('UPDATE ingestion_cursors SET last_success=? WHERE scope=?', (utc_iso(self._now()), scope.key))
            db.commit()
        return self.coverage(scope)

    def reconcile_window(self, scope, start, end, seen_ids, *,
                         after_message_id=None, through_message_id=None, observed_before=None):
        """Tombstone missing messages only after an authorized complete scan.

        Times bound message creation. Page boundaries disambiguate snowflakes
        sharing a timestamp. The scan start protects concurrent live updates.
        """
        first, last = utc_iso(start), utc_iso(end)
        seen = {str(identifier) for identifier in seen_ids}
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            rows = db.execute('SELECT message_id FROM messages WHERE scope=? AND deleted=0 '
                              'AND created_at>=? AND created_at<=? AND observed_at<=?',
                              (scope.key, first, last, utc_iso(observed_before) if observed_before else last)).fetchall()
            count = 0
            for row in rows:
                identifier = row['message_id']
                if identifier in seen:
                    continue
                if after_message_id is not None or through_message_id is not None:
                    if not identifier.isdecimal():
                        continue
                    if after_message_id is not None and int(identifier) <= int(after_message_id):
                        continue
                    if through_message_id is not None and int(identifier) > int(through_message_id):
                        continue
                count += int(self._delete_record(db, scope, identifier, utc_iso(self._now())))
            db.commit()
            return count

    def coverage(self, scope: Scope):
        cutoff = self._cutoff()
        with self._db() as db:
            row = db.execute('SELECT * FROM ingestion_cursors WHERE scope=?', (scope.key,)).fetchone()
            bounds = db.execute('SELECT MIN(created_at),MAX(created_at) FROM messages '
                                'WHERE scope=? AND deleted=0 AND created_at>=?', (scope.key, cutoff)).fetchone()
            windows = db.execute('SELECT * FROM coverage_windows WHERE scope=? AND end_at>=? ORDER BY start_at,end_at',
                                 (scope.key, cutoff)).fetchall()
        result = dict(row) if row else {'scope': scope.key, 'cursor': None, 'first_at': None,
                                      'last_at': None, 'last_success': None, 'complete': 0, 'gaps': '[]'}
        intervals = []
        for window in windows:
            if not window['complete']:
                continue
            first, last = max(window['start_at'], cutoff), window['end_at']
            if intervals and intervals[-1]['end'] >= first:
                intervals[-1]['end'] = max(intervals[-1]['end'], last)
            else:
                intervals.append({'start': first, 'end': last})
        requested_first = min((max(window['start_at'], cutoff) for window in windows), default=None)
        requested_last = max((window['end_at'] for window in windows), default=None)
        gaps = list(json.loads(result['gaps']))
        position = requested_first
        if position is not None:
            missing = []
            for interval in intervals:
                if position < interval['start']:
                    missing.append((position, interval['start']))
                position = max(position, interval['end'])
            if position < requested_last:
                missing.append((position, requested_last))
            for first, last in missing:
                reasons = [window['reason'] for window in windows if not window['complete'] and
                           window['start_at'] < last and window['end_at'] > first and window['reason']]
                gaps.append({'start': first, 'end': last, 'reason': reasons[-1] if reasons else 'unscanned_interval'})
        result['gaps'] = tuple(gaps)
        result['intervals'] = tuple(intervals)
        result['requested_first_at'], result['requested_last_at'] = requested_first, requested_last
        result['first_at'] = intervals[0]['start'] if intervals else bounds[0]
        result['last_at'] = intervals[-1]['end'] if intervals else bounds[1]
        result['complete'] = bool(intervals and not gaps)
        result['offline_mutations_complete'] = False
        return result

    def is_covered(self, scope, start, end):
        first, last = utc_iso(start), utc_iso(end)
        return any(interval['start'] <= first and interval['end'] >= last
                   for interval in self.coverage(scope)['intervals'])

    def prepare_outbound(self, snapshot: ConversationSnapshot, dedupe_key):
        now = utc_iso(self._now())
        with self._db() as db:
            result = db.execute('INSERT OR IGNORE INTO outbound_responses VALUES(?,?,?,?,?,?,?,?,?)',
                                (str(dedupe_key), snapshot.scope.key, snapshot.turn_id, snapshot.revision,
                                 json.dumps(snapshot.dependencies), 'prepared', now, now, None))
            db.commit()
            return bool(result.rowcount)

    def commit_outbound(self, dedupe_key):
        with self._db() as db:
            result = db.execute("UPDATE outbound_responses SET status='committed',updated_at=? "
                                "WHERE dedupe_key=? AND status='prepared'",
                                (utc_iso(self._now()), str(dedupe_key)))
            db.commit()
            return bool(result.rowcount)

    def mark_outbound(self, dedupe_key, status, message_id=None):
        if status not in {'sent', 'uncertain', 'abandoned'}:
            raise ValueError('Invalid outbound terminal status')
        with self._db() as db:
            result = db.execute('UPDATE outbound_responses SET status=?,updated_at=?,message_id=? '
                                'WHERE dedupe_key=? AND status IN (\'prepared\',\'committed\',\'uncertain\')',
                                (status, utc_iso(self._now()), str(message_id) if message_id is not None else None,
                                 str(dedupe_key)))
            db.commit()
            return bool(result.rowcount)

    def outbound(self, dedupe_key):
        with self._db() as db:
            row = db.execute('SELECT * FROM outbound_responses WHERE dedupe_key=?', (str(dedupe_key),)).fetchone()
        return dict(row) if row else None

    def uncertain(self, scope=None):
        with self._db() as db:
            if scope is None:
                rows = db.execute("SELECT * FROM outbound_responses WHERE status IN ('committed','uncertain')").fetchall()
            else:
                rows = db.execute("SELECT * FROM outbound_responses WHERE scope=? AND status IN ('committed','uncertain')",
                                  (scope.key,)).fetchall()
        return tuple(dict(row) for row in rows)

    def purge(self):
        with self._db() as db:
            rows = db.execute('SELECT scope,message_id,payload FROM messages WHERE deleted=0 AND created_at<?',
                              (self._cutoff(),)).fetchall()
        for row in rows:
            self.delete(_envelope(row['payload']).scope, row['message_id'])
        return len(rows)

    def backup_to(self, path):
        """SQLite online backup; copies WAL state coherently for migration/rollback."""
        destination = Path(path)
        if destination.resolve() == self.path.resolve():
            raise ValueError('Backup must use another path')
        destination.parent.mkdir(parents=True, exist_ok=True)
        with self._db() as source:
            with closing(sqlite3.connect(destination)) as target:
                source.backup(target)
        destination.chmod(0o600)
        return destination

    def close(self):
        # No persistent connection is held. Kept for lifecycle symmetry.
        return None
