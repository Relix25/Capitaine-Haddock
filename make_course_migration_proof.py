"""Verify the received files against the VPS evidence and create a safe migration record."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SNAPSHOTS = ROOT/'.extraction/Actualisation_2026-11-12/snapshots'


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream,'sha256').hexdigest()


def main():
    master = json.loads((SNAPSHOTS/'master-vps-verification.json').read_text(encoding='utf-8-sig'))
    pc = json.loads((SNAPSHOTS/'pc-sync-live.json').read_text(encoding='utf-8-sig'))
    folder = pc['folder']
    assert folder['state']=='idle'
    for name in ('needFiles','needBytes','receiveOnlyChangedFiles','pullErrors','errors'):
        assert folder[name]==0, name
    for name in ('cours-watch.service','cours-watch-obsidian.service'):
        assert master['units'][name]['Result']=='success', name
        assert master['units'][name]['ExecMainStatus']=='0', name
    assert master['report']['obsidian_export']['status']=='ok'
    assert all(source['status']=='ok' for source in master['report']['sources'])
    for relative,digest in master['automatic_file_hashes'].items():
        assert sha(ROOT/relative)==digest, relative
    pdfs=list((ROOT/'Documents - versions').glob('*/*.pdf'))
    assert len(pdfs)==master['pdfs']['published_pdfs']==master['pdfs']['current_documents']
    assert all(sha(path)==path.stem for path in pdfs)
    server='NXLAOUH-M3TZAVT-T5L6ZX4-U46AIY4-2JTGH2E-GLVDAJH-PR4MDMN-FN3HIAN'
    connection=pc['connections'][server]
    assert connection['connected'] is True
    archive=ROOT/'.extraction/Actualisation_2026-11-12/Cours seed VPS.tar'
    expected='6ef0545bfbdddcc833369bc6ee6bdcb14ded15c92c25b21062d8e30e94cac0e6'
    assert sha(archive)==expected
    manifest=json.loads((ROOT/'Corpus - manifeste.json').read_text(encoding='utf-8'))
    proof={'version':1,'verified_utc':datetime.now(timezone.utc).isoformat(),
        'syncthing':{'folder_id':'obsidian-cours','vps_mode':'sendonly','pc_mode':'receiveonly',
            'pc_state':folder['state'],'global_files':folder['globalFiles'],'global_bytes':folder['globalBytes'],
            'need_files':0,'need_bytes':0,'receive_only_changed_files':0,'pull_errors':0,
            'connected_device':server,'connected_via':connection['address']},
        'archive':{'sha256':expected,'files':manifest['file_count'],
            'bytes':manifest['total_staged_bytes'],'hashes_verified':True},
        'collection':{'checked_utc':master['report']['checked_utc'],
            'sources':master['report']['sources']},
        'pdfs':{'documents':master['pdfs']['current_documents'],'pages':master['pdfs']['current_pages'],
            'extra_guides':[{'filename':'AAIT Project Guidelines.pdf','pages':22,
            'sha256':'e2b8c20487322574475421c991d1d896a1d5be348b87f9737406f442f51b4c2b'}]},
        'received_automatic_hashes':master['automatic_file_hashes'],
        'root_note_hashes':{name:sha(ROOT/name) for name in ('Accueil Cours.md','Sources et couverture.md',
            'Actualisation - 12 novembre 2026.md','Synchronisation du coffre.md')}}
    output=SNAPSHOTS/'migration-verification.json'
    output.write_text(json.dumps(proof,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'proof':str(output),'automatic_hashes_match':True,'received_pdf_hashes_match':len(pdfs)}))


if __name__ == '__main__':
    main()
