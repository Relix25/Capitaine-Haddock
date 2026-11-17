"""Publish only pedagogical data to a separate read-only handoff for Obsidian."""
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import tempfile

COURSES = {'AAIT','AITDM','BDFA','SOS'}


def build_snapshot(data_dir,config,report,results=None):
    configured = {course['id']:course for course in config.get('courses',[]) if course['id'] in COURSES}
    observed = None if results is None else {}
    if results is not None:
        for result in results:
            observed[result.source] = [item.key for item in result.items] if result.status == 'ok' else []
    details = {source['source']:source.get('detail','') for source in report.get('sources',[])}
    snapshot = {'version':1,'checked_utc':report['checked_utc'],'courses':{}}
    with closing(sqlite3.connect('file:'+str(Path(data_dir)/'state.db')+'?mode=ro',uri=True)) as db:
        db.execute('BEGIN')
        for identity,course in configured.items():
            names = ('moodle:'+identity,'teams:'+identity)
            sources = [{'source':source,'status':status,'checked_utc':checked,'detail':details.get(source,'')}
                       for source,status,checked in db.execute('SELECT source,status,checked FROM sources WHERE source IN (?,?)',names)]
            items = [{'source':source,'fingerprint':fingerprint,**{key:value.get(key,'') for key in ('key','title','body','url')}}
                     for source,fingerprint,payload in db.execute('SELECT source,fingerprint,payload FROM items WHERE source IN (?,?) ORDER BY source,key',names)
                     for value in [json.loads(payload)]]
            entry = {'label':course['label'],'sources':sources,'items':items}
            if observed is not None:
                stored = {(item['source'], item['key']): item for item in items}
                entry['fresh_items'] = [stored[(source, key)] for source in names
                                        for key in observed.get(source, []) if (source, key) in stored]
            snapshot['courses'][identity] = entry
    return snapshot


def publish_snapshot(data_dir,config,report,results=None):
    settings = config.get('obsidian_export',{})
    if not settings.get('enabled',False): return {'status':'disabled'}
    if report.get('dry_run'): return {'status':'dry_run'}
    path = Path(settings.get('snapshot_file','/var/lib/cours-watch-publish/latest.json'))
    if path.name != 'latest.json' or path.is_symlink() or not path.parent.is_dir() or path.parent.is_symlink():
        raise ValueError('Invalid pedagogical handoff path')
    snapshot = build_snapshot(data_dir,config,report,results)
    payload = json.dumps(snapshot,ensure_ascii=False,indent=2)+'\n'
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',dir=path.parent,prefix='.snapshot-',delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(payload)
        temporary.chmod(0o640)
        os.replace(temporary,path)
    finally:
        if temporary and temporary.exists(): temporary.unlink()
    return {'status':'ok','courses':len(snapshot['courses']),'checked_utc':snapshot['checked_utc']}
