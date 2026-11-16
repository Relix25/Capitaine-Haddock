"""Read-only deployment checks; emits metadata, never session or API secrets."""
from datetime import datetime, timezone
import hashlib
import json
import re
from pathlib import Path
import subprocess
import xml.etree.ElementTree as ET


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream,'sha256').hexdigest()


def unit(name):
    output = subprocess.check_output(['systemctl','show',name,
        '--property=ActiveState,SubState,Result,ExecMainStatus,UnitFileState,NextElapseUSecRealtime'], text=True)
    return dict(line.split('=',1) for line in output.splitlines() if '=' in line)


def main():
    root = Path('/srv/obsidian/cours')
    report = json.loads(Path('/var/lib/cours-watch/reports/latest.json').read_text())
    snapshot_file = Path('/var/lib/cours-watch-publish/latest.json')
    snapshot = json.loads(snapshot_file.read_text()) if snapshot_file.exists() else None
    pdf_store = Path('/var/lib/cours-watch-publish/documents')
    catalogs = [json.loads(path.read_text()) for path in pdf_store.glob('*/catalog/*.json')]
    versions = {(c['course'],v['sha256']):v for c in catalogs for v in c['versions']}
    current = {(c['course'],c['current_sha']) for c in catalogs}
    pdfs = list(root.glob('Documents - versions/*/*.pdf'))
    notes = list(root.glob('Notes/*/Documents/*.md'))
    config = ET.parse('/srv/obsidian/.local/state/syncthing/config.xml').getroot()
    folder = next(f for f in config.findall('folder') if f.get('id') == 'obsidian-cours')
    automatic = [root/'Veille - dernier contrôle.md',
        *[root/'Notes'/c/f'{c} - Veille automatique.md' for c in ('AAIT','AITDM','BDFA','SOS')]]
    manual=[root/name for name in ('Accueil Cours.md','Sources et couverture.md',
        'Synchronisation du coffre.md','Actualisation - 12 novembre 2026.md','AGENTS.md',
        'Notes/AAIT/AAIT - Crafter Assignment - note de lecture 12 novembre 2026.md')]
    manifest=json.loads((root/'Corpus - manifeste.json').read_text())
    source_roots={'Advanced artificial intelligence techniques','AI for Trustworthy Decision Making',
                  'Big-Data Fundamentals and Analytics','Self-organizing systems'}
    protected=[entry for entry in manifest['files'] if entry['path'].split('/')[0] in source_roots]
    output = {'verified_utc':datetime.now(timezone.utc).isoformat(),
        'units':{name:unit(name) for name in ('cours-watch.service','cours-watch-bot.service',
            'cours-watch.timer','cours-watch-obsidian.path','cours-watch-obsidian.service',
            'syncthing@vaultsync.service')},
        'report':{'checked_utc':report['checked_utc'],'sources':report['sources'],
            'obsidian_export':report.get('obsidian_export')},
        'snapshot_checked_utc':snapshot['checked_utc'] if snapshot else None,
        'fresh_items':{c:len(v.get('fresh_items',[])) for c,v in snapshot['courses'].items()} if snapshot else {},
        'pdfs':{'catalogs':len(catalogs),'current_documents':len(current),
            'current_pages':sum(versions[key]['pages'] for key in current),
            'versions':len(versions),'published_pdfs':len(pdfs),'published_text_notes':len(notes),
            'published_hashes_valid':all(sha(path)==path.stem for path in pdfs)},
        'source_files':{'checked':len(protected),'unchanged':all(sha(root/e['path'])==e['staged_sha256'] for e in protected)},
        'text_pages_published':sum(len(re.findall(r'^### Page [0-9]+$',p.read_text(),re.M)) for p in notes),
        'syncthing':{'folder_id':folder.get('id'),'type':folder.get('type'),
            'path':folder.get('path'),'devices':[d.get('id') for d in folder.findall('device')],
            'all_folder_ids':[f.get('id') for f in config.findall('folder')]},
        'automatic_file_hashes':{str(path.relative_to(root)):sha(path) if path.exists() else None for path in automatic},
        'manual_file_hashes':{str(path.relative_to(root)):sha(path) if path.exists() else None for path in manual}}
    print(json.dumps(output,ensure_ascii=False,indent=2))


if __name__ == '__main__':
    main()
