"""Publish non-secret activation evidence only after real, successful service execution."""
from pathlib import Path
import json
import sqlite3
import subprocess

root = Path('/var/lib/cours-watch')
report = json.loads((root / 'reports/latest.json').read_text())
if report.get('dry_run') or len(report['sources']) != 8 or any(s['status'] != 'ok' for s in report['sources']):
    raise SystemExit('Activation not validated: eight successful live sources required')
for unit in ('cours-watch.service', 'cours-watch-session.service'):
    properties = subprocess.check_output(['systemctl', 'show', unit, '-p', 'Result', '-p', 'ExecMainStatus', '-p', 'ActiveState'], text=True)
    if 'Result=success' not in properties or 'ExecMainStatus=0' not in properties or 'ActiveState=inactive' not in properties:
        raise SystemExit('Service execution incomplete or unsuccessful: ' + unit)
session = json.loads((root / 'reports/moodle-session.json').read_text())
if session['status'] != 'ok' or report.get('delivery', {}).get('status') != 'ok':
    raise SystemExit('Session or delivery validation incomplete')
db = sqlite3.connect(root / 'state.db')
if db.execute('SELECT COUNT(*) FROM sources WHERE initialized=1 AND status="ok"').fetchone()[0] != 8:
    raise SystemExit('Eight initialized references required')
if db.execute('SELECT COUNT(*) FROM outbox WHERE delivered IS NULL').fetchone()[0]:
    raise SystemExit('Undelivered notifications remain')
if Path('/etc/nginx/snippets/cours-watch-private.conf').exists() or (root / 'login.active').exists():
    raise SystemExit('Temporary login interface must be closed')
if not Path('/etc/cours-watch/ready').exists():
    raise SystemExit('Activation gate is absent')
for timer in ('cours-watch.timer', 'cours-watch-session.timer'):
    subprocess.run(['systemctl', 'is-enabled', '--quiet', timer], check=True)
    subprocess.run(['systemctl', 'is-active', '--quiet', timer], check=True)
evidence = {'checked_utc': report['checked_utc'], 'sources': report['sources'],
            'items': sum(s['items'] for s in report['sources']), 'delivery': report['delivery'],
            'moodle_session': session['status'], 'login_closed': True, 'timers_active': True}
output = Path('/tmp/cours-watch-activation.json')
output.write_text(json.dumps(evidence, ensure_ascii=False, indent=2))
output.chmod(0o644)
print(json.dumps(evidence, ensure_ascii=False, indent=2))
