"""SQLite observation state, durable outbox and delivery receipts."""
from __future__ import annotations
import json
import sqlite3
import time
import uuid
import urllib.parse
from dataclasses import asdict
from courswatch.models import Item, Result, stamp, digest, canonical_forum_url


class State:
    def __init__(self, path):
        from courswatch.storage.schema import migrate
        self.db = sqlite3.connect(path)
        try:
            self.db.execute('PRAGMA journal_mode=WAL')
            migrate(self.db, 'state')
        except BaseException:
            self.db.close()
            raise

    def enqueue(self, event_key, event):
        cursor = self.db.execute('INSERT OR IGNORE INTO outbox(event_key,payload,created) VALUES(?,?,?)',
                                 (event_key, json.dumps(event, ensure_ascii=False), stamp()))
        self.db.commit()
        return bool(cursor.rowcount)

    def ingest(self, result: Result):
        row = self.db.execute('SELECT status,initialized FROM sources WHERE source=?', (result.source,)).fetchone()
        initialized = bool(row and row[1])
        previous_status = row[0] if row else None
        events = []
        if result.status != 'ok':
            # An unavailable source is never ingested as an empty successful snapshot.
            if previous_status != result.status:
                events.append({'kind': 'source_error', 'course': result.course,
                               'source': result.source, 'title': 'Vérification impossible',
                               'body': result.detail, 'url': ''})
        else:
            if previous_status and previous_status != 'ok':
                events.append({'kind': 'source_recovered', 'course': result.course,
                               'source': result.source, 'title': 'Accès rétabli', 'body': '', 'url': ''})
            for item in result.items:
                if item.key.startswith('forum:'):
                    canonical = canonical_forum_url(item.url)
                    if canonical:
                        fragment = urllib.parse.urlsplit(item.url).fragment
                        item = Item(item.key, item.title, item.body, canonical + ('#' + fragment if fragment else ''))
                old = self.db.execute('SELECT fingerprint FROM items WHERE source=? AND key=?',
                                      (result.source, item.key)).fetchone()
                first_document_check = not old and item.key.endswith((':document', ':metadata'))
                if initialized and not first_document_check and (not old or old[0] != item.fingerprint):
                    events.append({'kind': 'modified' if old else 'new', 'course': result.course,
                                   'source': result.source, 'title': item.title,
                                   'body': item.body, 'url': item.url})
                self.db.execute('INSERT OR REPLACE INTO items VALUES(?,?,?,?)',
                                (result.source, item.key, item.fingerprint,
                                 json.dumps(asdict(item), ensure_ascii=False)))
            generation = uuid.uuid4().hex
            self.db.execute('INSERT OR REPLACE INTO source_observations VALUES(?,?)', (result.source, generation))
            verified = {item.key for item in result.items if item.key.endswith(':document')}
            failed = {item.key[:-len(':document-status')] + ':document'
                      for item in result.items if item.key.endswith(':document-status')} - verified
            for key in verified | failed:
                self.db.execute('INSERT OR REPLACE INTO document_checks VALUES(?,?,?,?,?)',
                                (result.source, key, 'ok' if key in verified else 'error', stamp(), generation))
            initialized = True
            # Missing items are not deletions: the browser UI may virtualize older posts.
        for event in events:
            event_key = digest([result.source, event, stamp(), time.time_ns()])
            self.db.execute('INSERT INTO outbox(event_key,payload,created) VALUES(?,?,?)',
                            (event_key, json.dumps(event, ensure_ascii=False), stamp()))
        self.db.execute('INSERT OR REPLACE INTO sources VALUES(?,?,?,?)',
                        (result.source, result.status, int(initialized), stamp()))
        self.db.commit()
        return events

    def pending(self):
        return [(row[0], json.loads(row[1])) for row in self.db.execute(
            'SELECT id,payload FROM outbox WHERE delivered IS NULL ORDER BY id')]

    def sent(self, event_id, destination):
        return self.db.execute('SELECT 1 FROM deliveries WHERE event_id=? AND destination=?',
                               (event_id, destination)).fetchone() is not None

    def mark_destination(self, event_id, destination, receipt=None):
        self.db.execute('INSERT OR REPLACE INTO deliveries VALUES(?,?,?)', (event_id, destination, stamp()))
        if receipt and receipt.get('id'):
            self.db.execute('INSERT OR REPLACE INTO discord_receipts VALUES(?,?,?,?,?)',
                            (event_id, destination, receipt['id'], receipt.get('channel_id', ''),
                             json.dumps(receipt.get('mention_roles', []))))
        self.db.commit()

    def mark_complete(self, event_id):
        self.db.execute('UPDATE outbox SET delivered=? WHERE id=?', (stamp(), event_id))
        self.db.commit()

