"""Install the reviewed current calendar, bind announcement hashes, and keep secrets separate."""
import json
import os
from pathlib import Path
import sqlite3
from courswatch.reminders import body_hash

def main():
    import grp
    if os.geteuid() != 0:
        raise SystemExit('root required')
    target = Path('/etc/cours-watch/reminders.json')
    if target.exists():
        raise SystemExit('Existing reminder configuration not overwritten')
    configuration = json.loads(Path('/tmp/cours-watch-updated/reminders.json').read_text())
    db = sqlite3.connect('file:/var/lib/cours-watch/state.db?mode=ro', uri=True)
    for task in configuration['tasks']:
        reference = task['reference']
        if reference.pop('capture_body_hash', False):
            row = db.execute('SELECT payload FROM items WHERE source=? AND key=?', (reference['source'], reference['key'])).fetchone()
            if not row:
                raise SystemExit('Missing observed announcement: ' + task['id'])
            reference['body_hash'] = body_hash(json.loads(row[0])['body'])
    os.umask(0o077)
    target.write_text(json.dumps(configuration, ensure_ascii=False, indent=2) + '\n')
    os.chown(target, 0, grp.getgrnam('courswatch').gr_gid)
    target.chmod(0o640)
    print(json.dumps({'installed': True, 'tasks': len(configuration['tasks']), 'important_days': 3, 'urgent_days': 1}))


if __name__ == "__main__":
    main()
