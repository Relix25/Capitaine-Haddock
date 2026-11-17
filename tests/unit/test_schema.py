"""Legacy schema upgrades, idempotence, rollback compatibility and failure atomicity."""
import json
import sqlite3
import unittest

from courswatch.storage.schema import migrate


class SchemaTests(unittest.TestCase):
    def test_legacy_forum_is_upgraded_once_without_losing_outbox_or_receipts(self):
        db = sqlite3.connect(':memory:')
        self.addCleanup(db.close)
        db.execute('CREATE TABLE items(source TEXT,key TEXT,fingerprint TEXT,payload TEXT,PRIMARY KEY(source,key))')
        item = {'key': 'forum:p1', 'title': 'Title', 'body': 'Original',
                'url': 'https://curs.upb.ro/2026/mod/forum/discuss.php?d=1&parent=2#p1'}
        db.execute('INSERT INTO items VALUES(?,?,?,?)', ('moodle:SOS', item['key'], 'old', json.dumps(item)))
        db.commit()
        migrate(db, 'state')
        upgraded = db.execute('SELECT payload FROM items').fetchone()[0]
        self.assertEqual(json.loads(upgraded)['url'], 'https://curs.upb.ro/2026/mod/forum/discuss.php?d=1#p1')
        db.execute('INSERT INTO outbox(event_key,payload,created) VALUES(?,?,?)', ('keep', '{}', 'known'))
        db.execute('INSERT INTO discord_receipts VALUES(?,?,?,?,?)', (1, 'channel', 'message', 'channel', '[]'))
        db.commit()
        migrate(db, 'state')
        self.assertEqual(db.execute('SELECT COUNT(*) FROM schema_migrations').fetchone()[0], 3)
        self.assertEqual(db.execute('SELECT event_key FROM outbox').fetchone()[0], 'keep')
        self.assertEqual(db.execute('SELECT message_id FROM discord_receipts').fetchone()[0], 'message')
        # Old code can still use its positional insert into the original schema.
        db.execute('INSERT OR REPLACE INTO sources VALUES(?,?,?,?)', ('moodle:SOS', 'ok', 1, 'known'))

    def test_future_schema_is_rejected_without_modification(self):
        db = sqlite3.connect(':memory:')
        self.addCleanup(db.close)
        db.execute('CREATE TABLE schema_migrations(component TEXT,version INTEGER,applied_utc TEXT,PRIMARY KEY(component,version))')
        db.execute('INSERT INTO schema_migrations VALUES(?,?,?)', ('state', 999, 'future'))
        db.commit()
        before = list(db.iterdump())
        with self.assertRaises(RuntimeError):
            migrate(db, 'state')
        self.assertEqual(list(db.iterdump()), before)

    def test_invalid_legacy_payload_rolls_back_the_entire_migration(self):
        db = sqlite3.connect(':memory:')
        self.addCleanup(db.close)
        db.execute('CREATE TABLE items(source TEXT,key TEXT,fingerprint TEXT,payload TEXT,PRIMARY KEY(source,key))')
        db.execute('INSERT INTO items VALUES(?,?,?,?)', ('moodle:SOS', 'forum:p1', 'keep', '{invalid'))
        db.commit()
        before = list(db.iterdump())
        with self.assertRaises(ValueError):
            migrate(db, 'state')
        self.assertEqual(list(db.iterdump()), before)


if __name__ == '__main__':
    unittest.main()
