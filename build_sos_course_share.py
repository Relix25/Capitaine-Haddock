"""Prepare a shareable SOS teaching-material snapshot from the master handoff."""
import argparse
from datetime import datetime
import hashlib
import html
import json
import os
from pathlib import Path
import pwd
import re
import tempfile
import zipfile
from zoneinfo import ZoneInfo

MASTER=Path('/srv/obsidian/cours')
STORE=Path('/var/lib/cours-watch-publish/documents')
NAMES={'module-30294':'sos-curs1.pdf','module-30299':'sos-project description.pdf',
       'module-30300':'Project examples.pdf','module-38386':'sos-curs2.pdf',
       'module-47886':'sos-curs3.pdf','module-59216':'sos-curs4.pdf','module-59217':'sos-curs5.pdf'}


def digest(data): return hashlib.sha256(data).hexdigest()


def write_new(path,data):
    with path.open('xb') as stream:
        stream.write(data)
    path.chmod(0o660)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply',action='store_true')
    parser.add_argument('--not-before',help='Minimum collection timestamp in ISO UTC')
    args=parser.parse_args()
    snapshot=json.loads(Path('/var/lib/cours-watch-publish/latest.json').read_text())
    checked=datetime.fromisoformat(snapshot['checked_utc'].replace('Z','+00:00'))
    if args.not_before and checked<datetime.fromisoformat(args.not_before.replace('Z','+00:00')):
        raise RuntimeError('Waiting for the requested new collection')
    local=checked.astimezone(ZoneInfo('Europe/Bucharest'))
    if local.date()!=datetime.now(ZoneInfo('Europe/Bucharest')).date():
        raise RuntimeError('A current-day collection is required')
    course=snapshot['courses']['SOS']
    statuses={s['source']:s for s in course['sources']}
    if any(statuses.get(s,{}).get('status')!='ok' for s in ('moodle:SOS','teams:SOS')):
        raise RuntimeError('Both SOS sources must have a successful collection')
    fresh={(item['source'],item['key']):item for item in course['fresh_items']}
    fresh_hashes={m.group(1) for item in course['fresh_items'] if item['source']=='moodle:SOS'
                  for m in [re.fullmatch(r'SHA256 ([0-9a-f]{64})',item.get('body',''))] if m}
    planned=[]
    filenames=set()
    for path in sorted((STORE/'SOS/catalog').glob('*.json')):
        if path.is_symlink(): raise RuntimeError('Catalog symlink refused')
        catalog=json.loads(path.read_text())
        if catalog['course']!='SOS': raise RuntimeError('Unexpected course')
        sha=catalog['current_sha']
        if not re.fullmatch('[0-9a-f]{64}',sha): raise RuntimeError('Invalid hash')
        version=next(v for v in reversed(catalog['versions']) if v['sha256']==sha)
        if version['relative_file']!=f'SOS/files/{sha}.pdf': raise RuntimeError('Invalid PDF path')
        source=STORE/version['relative_file']
        if source.is_symlink(): raise RuntimeError('PDF symlink refused')
        data=source.read_bytes()
        if digest(data)!=sha or len(data)!=version['bytes']: raise RuntimeError('PDF hash mismatch')
        if sha not in fresh_hashes: raise RuntimeError('PDF not verified by latest collection: '+catalog['key'])
        from pypdf import PdfReader
        import io
        pages=len(PdfReader(io.BytesIO(data)).pages)
        if pages!=version['pages']: raise RuntimeError('Page count mismatch')
        name=NAMES.get(catalog['key'])
        if not name:
            title=re.sub(r'[^A-Za-z0-9 _.-]+','-',catalog['title']).strip(' .')[:100] or 'SOS'
            name=title+'-'+sha[:8]+'.pdf'
        if name.casefold() in filenames: raise RuntimeError('Duplicate export filename')
        filenames.add(name.casefold())
        planned.append({'name':name,'data':data,'source_url':catalog['source_url'],
                        'title':catalog['title'],'key':catalog['key'],'sha256':sha,'pages':pages,
                        'archive_date_utc':version['downloaded_utc']})
    if len(planned)<7: raise RuntimeError('Expected at least seven SOS PDFs')
    announcements=[]
    for item in course['items']:
        if not item.get('body','').strip(): continue
        if item['source']=='teams:SOS' or any(x in item.get('url','') for x in ('/mod/forum/discuss.php','/mod/assign/')):
            if ':document' in item['key']: continue
            announcements.append({**{key:item.get(key,'') for key in ('source','key','title','body','url')},
                                   'observed_in_latest_collection':(item['source'],item['key']) in fresh})
    shared_path=Path('/var/lib/cours-watch-publish/sos-shared-audit.json')
    shared=json.loads(shared_path.read_text())
    if shared.get('course')!='SOS' or shared.get('status')!='empty':
        raise RuntimeError('Shared files must be inspected before finalizing this pack')
    shared_check={key:shared[key] for key in ('course','checked_utc','area','status')}
    stamp=local.strftime('%Y-%m-%d-%H%M%S')
    course_dir=MASTER/'Self-organizing systems'
    if course_dir.is_symlink(): raise RuntimeError('Course symlink refused')
    folder=course_dir/('Partage - '+stamp)
    archive=course_dir/('SOS-documents-Moodle-Teams-'+stamp+'.zip')
    meta={'course':'SOS','checked_utc':snapshot['checked_utc'],
          'checked_bucharest':local.isoformat(),'sources':course['sources'],
          'documents':[{k:v for k,v in record.items() if k!='data'} for record in planned],
          'announcements':announcements,
          'teams_shared_file_check':shared_check,
          'limits':['Teams: publications et réponses chargées dans Général uniquement.',
             'Le dossier Général/Partagé a été contrôlé séparément et était vide.',
             'Autres canaux et Classwork hors couverture.',
             'Moodle: activités, pièces jointes et jusqu’à 40 discussions par forum.',
             'Les dates de collecte ou archivage ne sont pas les dates de publication.',
             'Le planning partagé et le créneau personnel ne sont pas vérifiés.']}
    print(json.dumps({'apply':args.apply,'checked_utc':snapshot['checked_utc'],'pdfs':len(planned),
        'pages':sum(p['pages'] for p in planned),'announcements':len(announcements),
        'folder':str(folder),'archive':str(archive)},ensure_ascii=False),flush=True)
    if not args.apply: return
    if os.geteuid()!=pwd.getpwnam('vaultagent').pw_uid: raise RuntimeError('Run as vaultagent')
    if folder.exists() or archive.exists(): raise RuntimeError('Export already exists; no overwrite')
    folder.mkdir(mode=0o770)
    for record in planned: write_new(folder/record['name'],record['data'])
    stamp_text=local.strftime('%d/%m/%Y à %H:%M:%S (Bucarest)')
    readme=f'''SOS — Documents enseignants Moodle et Teams
Contrôle : {stamp_text}

Ce paquet contient {len(planned)} PDF de cours/consignes ({sum(p['pages'] for p in planned)} pages),
les annonces et les informations de rendu collectées sur Moodle et Teams.
Ouvrir Annonces-et-echeances.html dans un navigateur pour les messages et liens.
Sources.json donne les sources, empreintes SHA256, pages et limites de couverture.
Le dossier Général / Partagé de Teams a également été inspecté : il était vide.
Date de ce contrôle séparé (UTC) : {shared_check['checked_utc']}.
Les PDF ont été retéléchargés et vérifiés lors du dernier contrôle, même à nom
ou URL identiques. Les originaux et anciennes versions restent conservés.

Contenu :
'''+''.join(f"- {p['name']} — {p['pages']} pages\n" for p in planned)+'''
Le paquet rassemble les supports enseignants ; il ne contient ni présentation
personnelle, ni projet étudiant, ni dataset, ni donnée de connexion.

Limites : Teams couvre seulement les publications/réponses chargées dans Général.
Le dossier Général/Partagé a été vérifié ; les autres canaux et Classwork
ne sont pas garantis.
Moodle respecte les limites de collecte indiquées dans Sources.json.
La date de contrôle ne prouve pas la date de publication d'un document.
Les horaires Moodle sont transcrits tels qu'affichés ; leur fuseau n'est pas
explicitement établi. Aucun créneau personnel, dépôt ou présence n'est déduit.
'''
    write_new(folder/'LIRE-MOI.txt',readme.encode('utf-8'))
    sections=[]
    esc=html.escape
    for item in announcements:
        state='Observé dans la dernière collecte' if item['observed_in_latest_collection'] else 'Élément conservé ; dernière observation individuelle inconnue'
        sections.append(f'<article><h2>{esc(item["title"])}</h2><p>{esc(item["source"])} · {state}</p><p><a href="{esc(item["url"],quote=True)}">Ouvrir la source</a></p><div class="body">{esc(item["body"])}</div></article>')
    page='''<!doctype html><html lang="fr"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>SOS — Annonces Moodle et Teams</title><style>body{font:17px/1.6 system-ui;max-width:950px;margin:40px auto;padding:0 20px;color:#182b36}article{border-top:1px solid #cbd5df;padding:20px 0}.body{white-space:pre-wrap;overflow-wrap:anywhere}a{color:#075985}</style>'''
    page+=f'<h1>SOS — Annonces et informations de rendu</h1><p>Contrôle : {esc(stamp_text)}. Texte original des publications.</p>'
    page+=''.join(sections)+'<h2>Couverture</h2><p>'+esc(' '.join(meta['limits']))+'</p></html>'
    write_new(folder/'Annonces-et-echeances.html',page.encode('utf-8'))
    write_new(folder/'Sources.json',(json.dumps(meta,ensure_ascii=False,indent=2)+'\n').encode())
    fd,temp=tempfile.mkstemp(prefix='.sos-share-',suffix='.tmp',dir=course_dir)
    os.close(fd)
    temporary=Path(temp)
    try:
        with zipfile.ZipFile(temporary,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as z:
            for path in sorted(folder.iterdir()): z.write(path,arcname=path.name)
        with zipfile.ZipFile(temporary) as z:
            if z.testzip() is not None: raise RuntimeError('Archive verification failed')
            for record in planned:
                if digest(z.read(record['name']))!=record['sha256']: raise RuntimeError('Archive PDF hash mismatch')
        temporary.chmod(0o660)
        os.replace(temporary,archive)
    finally: temporary.unlink(missing_ok=True)
    print(json.dumps({'created':True,'folder':str(folder),'archive':str(archive),
       'archive_sha256':digest(archive.read_bytes()),'bytes':archive.stat().st_size},ensure_ascii=False))


if __name__=='__main__': main()
