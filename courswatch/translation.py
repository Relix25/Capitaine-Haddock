"""Offline French translations in a separate cache; original snapshots never change."""
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess

ENGINE = 'argos-1.9-ct2-fr-v4'


def settings():
    try:
        value = json.loads(Path(os.environ.get('COURS_WATCH_TRANSLATION_CONFIG','/etc/cours-watch/translation.json')).read_text())
        return value if isinstance(value,dict) else {}
    except (OSError,ValueError):
        return {}


def eligible(event):
    if event.get('kind') not in ('new','modified') or not str(event.get('body','')).strip():
        return False
    source = event.get('source','')
    if source.startswith('teams:'):
        return not event.get('title','').strip().endswith('terminé')
    return source.startswith('moodle:') and '/mod/forum/discuss.php' in event.get('url','')


def cache_key(event):
    value = [ENGINE,event.get('title',''),event.get('body','')]
    return hashlib.sha256(json.dumps(value,ensure_ascii=False).encode()).hexdigest()


def database(data_dir):
    path = data_dir / 'translations.db'
    db = sqlite3.connect(path,timeout=10)
    path.chmod(0o600)
    db.execute('CREATE TABLE IF NOT EXISTS translations(key TEXT PRIMARY KEY,payload TEXT NOT NULL,checked TEXT NOT NULL)')
    return db


def with_cached_translation(event,data_dir):
    if not eligible(event) or not (data_dir / 'translations.db').exists():
        return event
    try:
        with closing(sqlite3.connect('file:' + str(data_dir / 'translations.db') + '?mode=ro',uri=True)) as db:
            row = db.execute('SELECT payload FROM translations WHERE key=?',(cache_key(event),)).fetchone()
        if row:
            return {**event,'translation':json.loads(row[0])}
    except (sqlite3.Error,ValueError):
        pass
    return event


def prepare_events(events,data_dir,runner=None):
    cfg = settings()
    if not cfg.get('enabled') or not any(eligible(event) for event in events):
        return events
    output = [dict(event) for event in events]
    missing = {}
    with closing(database(data_dir)) as db:
        for event in output:
            if not eligible(event):
                continue
            key = cache_key(event)
            row = db.execute('SELECT payload FROM translations WHERE key=?',(key,)).fetchone()
            cached = json.loads(row[0]) if row else None
            if cached and cached.get('status') in ('translated','not_needed'):
                event['translation'] = cached
            else:
                missing[key] = {'key':key,'title':event.get('title',''),'body':event.get('body','')}
        results = {}
        failure = None
        if missing:
            try:
                request = {'items':list(missing.values()),'models':cfg.get('models','/opt/cours-watch/models')}
                if runner:
                    records = runner(request)
                else:
                    result = subprocess.run([cfg.get('python','/opt/cours-watch/translation-venv/bin/python'),
                        cfg.get('worker','/opt/cours-watch/translate_worker.py')],
                        input=json.dumps(request,ensure_ascii=False),text=True,capture_output=True,
                        timeout=cfg.get('timeout_seconds',180),check=True,
                        env={'PATH':'/usr/bin:/bin','LANG':'C.UTF-8','OMP_NUM_THREADS':'2','OPENBLAS_NUM_THREADS':'1'})
                    records = json.loads(result.stdout)
                for record in records:
                    if record.get('key') not in missing or record.get('status') not in ('translated','not_needed','failed'):
                        raise ValueError('Invalid translation result')
                    results[record['key']] = record
            except (OSError,subprocess.SubprocessError,ValueError,TypeError):
                failure = 'engine_unavailable'
            for key in missing:
                record = results.get(key,{'key':key,'status':'failed','reason':failure or 'missing_result'})
                record.update(engine=ENGINE,checked_utc=datetime.now(timezone.utc).isoformat(timespec='seconds'))
                db.execute('INSERT OR REPLACE INTO translations VALUES(?,?,?)',
                           (key,json.dumps(record,ensure_ascii=False),record['checked_utc']))
                results[key] = record
            db.commit()
        for event in output:
            if eligible(event) and cache_key(event) in results:
                event['translation'] = results[cache_key(event)]
    report = {'checked_utc':datetime.now(timezone.utc).isoformat(timespec='seconds'),
              'engine':ENGINE,'translated':sum(e.get('translation',{}).get('status') == 'translated' for e in output),
              'already_french':sum(e.get('translation',{}).get('status') == 'not_needed' for e in output),
              'failed':sum(e.get('translation',{}).get('status') == 'failed' for e in output),
              'newly_processed':len(missing),'offline':True}
    directory = data_dir / 'reports'
    directory.mkdir(exist_ok=True)
    pending = directory / 'translation.pending'
    pending.write_text(json.dumps(report,ensure_ascii=False,indent=2))
    pending.chmod(0o600)
    os.replace(pending,directory / 'translation.json')
    return output


def display_event(event):
    translation = event.get('translation',{})
    if translation.get('status') == 'translated':
        return {**event,'title':translation['title'],'body':translation['body']}
    return event
