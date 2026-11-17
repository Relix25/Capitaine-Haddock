"""Verify live collection, initial Discord receipts and scheduled reminder policy; no secrets."""


def main():
    import json
    from pathlib import Path
    import sqlite3
    import subprocess

    root = Path('/var/lib/cours-watch')
    report = json.loads((root / 'reports/latest.json').read_text())
    if report['dry_run'] or len(report['sources']) != 8 or any(s['status'] != 'ok' for s in report['sources']):
        raise SystemExit('Eight successful production sources required')
    properties = subprocess.check_output(['systemctl','show','cours-watch.service','-p','ActiveState','-p','ExecMainStatus','-p','Result'],text=True)
    if 'ActiveState=inactive' not in properties or 'ExecMainStatus=0' not in properties or 'Result=success' not in properties:
        raise SystemExit('Production service not successfully complete')
    db = sqlite3.connect(root / 'state.db')
    if db.execute('SELECT COUNT(*) FROM outbox WHERE delivered IS NULL').fetchone()[0]:
        raise SystemExit('Pending delivery remains')
    rows = db.execute('''SELECT r.message_id,r.channel_id,r.role_ids FROM discord_receipts r
     JOIN outbox o ON o.id=r.event_id WHERE o.event_key LIKE ? ORDER BY o.id''',
     ('initial-brief:2026-11-12:%',)).fetchall()
    if len(rows) != 4 or sum(bool(json.loads(r[2])) for r in rows) != 1:
        raise SystemExit('Expected four confirmed initial messages and one role mention')
    configuration = json.loads(Path('/etc/cours-watch/reminders.json').read_text())
    if not configuration['enabled'] or len(configuration['tasks']) != 10 or configuration['policy'] != {'important_days':3,'urgent_days':1,'send_hour':8}:
        raise SystemExit('Unexpected daily project reminder policy')
    documents = db.execute('SELECT COUNT(*) FROM items WHERE key LIKE ?', ('%:document',)).fetchone()[0]
    for timer in ('cours-watch.timer','cours-watch-session.timer'):
        subprocess.run(['systemctl','is-active','--quiet',timer],check=True)
    proof = {'checked_utc':report['checked_utc'],'sources':8,'items':sum(s['items'] for s in report['sources']),
             'latest_changes':len(report['changes']),'latest_delivery':report['delivery'],
             'document_fingerprints':documents,'tasks':10,'important_days':3,'urgent_days':1,
             'initial_messages':[{'message_id':r[0],'channel_id':r[1],'role_ids':json.loads(r[2])} for r in rows],
             'initial_pings':1,'pending_deliveries':0,'timers_active':True}
    output = Path('/tmp/cours-watch-reminder-proof.json')
    output.write_text(json.dumps(proof,ensure_ascii=False,indent=2))
    output.chmod(0o644)
    print(json.dumps(proof,ensure_ascii=False,indent=2))


if __name__ == '__main__':
    main()
