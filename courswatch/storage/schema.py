"""Versioned migrations. Call only on an explicitly writable SQLite connection."""
import hashlib
import json
import re
from datetime import datetime, timezone
from urllib.parse import urlsplit, urlunsplit, parse_qs


def _state_base(db):
    statements = (
        'CREATE TABLE IF NOT EXISTS sources(source TEXT PRIMARY KEY,status TEXT,initialized INTEGER NOT NULL DEFAULT 0,checked TEXT)',
        'CREATE TABLE IF NOT EXISTS items(source TEXT,key TEXT,fingerprint TEXT,payload TEXT,PRIMARY KEY(source,key))',
        'CREATE TABLE IF NOT EXISTS outbox(id INTEGER PRIMARY KEY AUTOINCREMENT,event_key TEXT UNIQUE,payload TEXT NOT NULL,delivered TEXT,created TEXT NOT NULL)',
        'CREATE TABLE IF NOT EXISTS deliveries(event_id INTEGER,destination TEXT,delivered TEXT NOT NULL,PRIMARY KEY(event_id,destination))',
        'CREATE TABLE IF NOT EXISTS discord_receipts(event_id INTEGER,destination TEXT,message_id TEXT,channel_id TEXT,role_ids TEXT,PRIMARY KEY(event_id,destination))',
    )
    for statement in statements:
        db.execute(statement)


def _canonical_forums(db):
    for source, key, payload in db.execute("SELECT source,key,payload FROM items WHERE key LIKE 'forum:%'").fetchall():
        item = json.loads(payload)
        parsed = urlsplit(item['url'])
        discussion = parse_qs(parsed.query).get('d', [''])[0]
        if parsed.hostname != 'curs.upb.ro' or not parsed.path.endswith('/mod/forum/discuss.php') or not discussion.isdigit():
            continue
        canonical = urlunsplit(('https', parsed.netloc, parsed.path, 'd=' + discussion, parsed.fragment))
        if canonical != item['url']:
            item['url'] = canonical
            clean = lambda value: re.sub(r'\s+', ' ', str(value or '')).strip()
            fingerprint = hashlib.sha256(json.dumps([clean(item['title']), clean(item['body']), canonical], ensure_ascii=False, sort_keys=True).encode()).hexdigest()
            db.execute('UPDATE items SET fingerprint=?,payload=? WHERE source=? AND key=?',
                       (fingerprint, json.dumps(item, ensure_ascii=False), source, key))


def _document_checks(db):
    # Separate tables keep the historical four-column sources/items contracts intact.
    db.execute('CREATE TABLE IF NOT EXISTS source_observations(source TEXT PRIMARY KEY,generation TEXT NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS document_checks(source TEXT,key TEXT,status TEXT NOT NULL,checked TEXT NOT NULL,generation TEXT NOT NULL,PRIMARY KEY(source,key))')


def _budget_base(db):
    db.execute('''CREATE TABLE IF NOT EXISTS calls(
        id INTEGER PRIMARY KEY,day TEXT NOT NULL,kind TEXT NOT NULL,status TEXT NOT NULL,
        reserved INTEGER NOT NULL,charged INTEGER NOT NULL,input_tokens INTEGER NOT NULL DEFAULT 0,
        output_tokens INTEGER NOT NULL DEFAULT 0,created TEXT NOT NULL,
        api_total_tokens INTEGER,cached_input_tokens INTEGER NOT NULL DEFAULT 0)''')
    db.execute('CREATE TABLE IF NOT EXISTS notices(day TEXT PRIMARY KEY)')
    db.execute('CREATE TABLE IF NOT EXISTS blocked_days(day TEXT PRIMARY KEY)')
    columns = {row[1] for row in db.execute('PRAGMA table_info(calls)')}
    if 'api_total_tokens' not in columns:
        db.execute('ALTER TABLE calls ADD COLUMN api_total_tokens INTEGER')
    if 'cached_input_tokens' not in columns:
        db.execute('ALTER TABLE calls ADD COLUMN cached_input_tokens INTEGER NOT NULL DEFAULT 0')


def _private_cards(db):
    db.execute('CREATE TABLE IF NOT EXISTS ui_messages(message_id TEXT PRIMARY KEY,user_id TEXT,payload TEXT)')


MIGRATIONS = {'state': (_state_base, _canonical_forums, _document_checks),
              'budget': (_budget_base,), 'private_cards': (_private_cards,)}


def migrate(db, component):
    """Atomically upgrade one component; old rows and legacy columns are preserved."""
    migrations = MIGRATIONS[component]
    if db.in_transaction:
        raise RuntimeError('Schema migration requires a connection outside a transaction')
    db.execute('BEGIN IMMEDIATE')
    try:
        db.execute('CREATE TABLE IF NOT EXISTS schema_migrations(component TEXT,version INTEGER,applied_utc TEXT,PRIMARY KEY(component,version))')
        applied = {row[0] for row in db.execute('SELECT version FROM schema_migrations WHERE component=?', (component,))}
        if applied - set(range(1, len(migrations) + 1)):
            raise RuntimeError('Database schema is newer than this application')
        for version, change in enumerate(migrations, 1):
            if version not in applied:
                change(db)
                db.execute('INSERT INTO schema_migrations VALUES(?,?,?)',
                           (component, version, datetime.now(timezone.utc).isoformat()))
        db.commit()
    except BaseException:
        db.rollback()
        raise
