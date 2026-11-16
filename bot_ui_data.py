"""Read course snapshots and bind private command cards to their requesting user."""
import json
from contextlib import closing
from pathlib import Path
import sqlite3
from types import SimpleNamespace
from course_reminders import reminder_events
from course_translation import with_cached_translation


def current_events(data_dir, config, course='tous'):
    settings = json.loads(Path(config.get('reminders_file','/etc/cours-watch/reminders.json')).read_text())
    with closing(sqlite3.connect('file:' + str(data_dir / 'state.db') + '?mode=ro', uri=True)) as db:
        _, events = reminder_events(SimpleNamespace(db=db), settings)
    selected = [event for _, label, _, event in events if course == 'tous' or label == course]
    for event in selected:
        event['mention'] = False
    return selected


def remember_private_card(data_dir, message_id, user_id, event):
    with closing(sqlite3.connect(data_dir / 'state.db')) as db:
        db.execute('CREATE TABLE IF NOT EXISTS ui_messages(message_id TEXT PRIMARY KEY,user_id TEXT,payload TEXT)')
        db.execute('INSERT OR REPLACE INTO ui_messages VALUES(?,?,?)',
                   (str(message_id),str(user_id),json.dumps(event,ensure_ascii=False)))
        db.commit()


def find_event(data_dir, message_id, user_id):
    with closing(sqlite3.connect('file:' + str(data_dir / 'state.db') + '?mode=ro',uri=True)) as db:
        row = db.execute('SELECT o.payload FROM discord_receipts r JOIN outbox o ON o.id=r.event_id WHERE r.message_id=?',
                          (str(message_id),)).fetchone()
        if row:
            return with_cached_translation(json.loads(row[0]),data_dir)
        exists = db.execute('SELECT 1 FROM sqlite_master WHERE type=? AND name=?', ('table','ui_messages')).fetchone()
        if exists:
            row = db.execute('SELECT user_id,payload FROM ui_messages WHERE message_id=?', (str(message_id),)).fetchone()
            if row and row[0] == str(user_id):
                return with_cached_translation(json.loads(row[1]),data_dir)
    return None
