"""Export authorized course evidence only, never session/authentication files."""


def main():
    from contextlib import closing
    from datetime import datetime, timezone
    import argparse
    import json
    from pathlib import Path
    import os
    import pwd
    import sqlite3

    parser = argparse.ArgumentParser()
    parser.add_argument('--output',required=True)
    args = parser.parse_args()
    output = Path(args.output)
    if output.resolve() != Path('/tmp/cours-evidence-20261112'):
        raise SystemExit('Unexpected evidence directory')
    output.mkdir(mode=0o700,exist_ok=True)
    uid = pwd.getpwnam('ubuntu').pw_uid
    os.chown(output,uid,-1)
    os.chmod(output,0o700)
    data = Path('/var/lib/cours-watch')
    report = json.loads((data/'reports/latest.json').read_text())
    reminders = json.loads(Path('/etc/cours-watch/reminders.json').read_text())
    with closing(sqlite3.connect('file:'+str(data/'state.db')+'?mode=ro',uri=True)) as db:
        db.execute('BEGIN')
        for course in ('AAIT','AITDM','BDFA','SOS'):
            sources = [dict(zip(('source','status','initialized','checked_utc'),row)) for row in db.execute(
                'SELECT source,status,initialized,checked FROM sources WHERE source IN (?,?)',('moodle:'+course,'teams:'+course))]
            items = [{'source':source,'fingerprint':fingerprint,**json.loads(payload)} for source,fingerprint,payload in db.execute(
                'SELECT source,fingerprint,payload FROM items WHERE source IN (?,?) ORDER BY source,key',('moodle:'+course,'teams:'+course))]
            history = []
            for identity,created,payload in db.execute('SELECT id,created,payload FROM outbox WHERE created>=? ORDER BY id',('2026-11-10',)):
                event = json.loads(payload)
                if event.get('source') not in ('moodle:'+course,'teams:'+course): continue
                receipt = db.execute('SELECT message_id,channel_id FROM discord_receipts WHERE event_id=? LIMIT 1',(identity,)).fetchone()
                history.append({'created_utc':created,**event,'discord_message_id':receipt[0] if receipt else None})
            document = {'course':course,'exported_utc':datetime.now(timezone.utc).isoformat(timespec='seconds'),
                'latest_collection_utc':report.get('checked_utc'),'sources':sources,'items':items,'changes_history':history,
                'configured_reminders':[task for task in reminders.get('tasks',[]) if task.get('course') == course],
                'coverage':{source['source']:source.get('detail') for source in report.get('sources',[]) if source['source'].endswith(':'+course)},
                'limitations':['Items retained when a virtualized UI does not display them; no per-item last-seen timestamp.',
                    'Teams: loaded General posts/replies only; unannounced files, other channels and Classwork not covered.',
                    'Moodle: bounded forum and file checks as documented by the collector.']}
            path = output/(course+'.json')
            path.write_text(json.dumps(document,ensure_ascii=False,indent=2),encoding='utf-8')
            path.chmod(0o600)
            os.chown(path,uid,-1)
            print(json.dumps({'course':course,'items':len(items),'sources':sources,'file':path.name}),flush=True)


if __name__ == '__main__':
    main()
