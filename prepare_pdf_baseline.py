"""Package the existing PDF bytes as an immutable comparison baseline, without changing them."""
import hashlib
import json
from pathlib import Path
import tarfile
from pypdf import PdfReader

root=Path(__file__).resolve().parents[2]
groups={
 'AAIT':('Advanced artificial intelligence techniques',[
 ('01_value_based.pdf',20524,'resource'),('02_pg.pdf',63049,'resource'),('03_exploration.pdf',63051,'resource'),
 ('04_model_based.pdf',63054,'resource'),('assignment_v0.pdf',35700,'assign'),('AAIT Project Guidelines.pdf',67666,'assign')]),
 'AITDM':('AI for Trustworthy Decision Making',[
 ('Lecture1 folder contents/Lecture1_2026.pdf',20710,'folder'),('Lecture2_2026.pdf',42958,'resource'),
 ('Lecture3-4_2026.pdf',62795,'resource'),('Lecture5_2026.pdf',62836,'resource')]),
 'BDFA':('Big-Data Fundamentals and Analytics',[
 ('Lecture1 folder contents/Big_Data-Introduction_2026.pdf',20714,'folder'),('Big_Data-Lecture_02_2026.pdf',44618,'resource'),
 ('Big_Data-Lecture_03_2026.pdf',51050,'resource')]),
 'SOS':('Self-organizing systems',[
 ('sos-project description.pdf',30299,'resource'),('Project examples.pdf',30300,'resource'),
 ('sos-curs1.pdf',30294,'resource'),('sos-curs2.pdf',38386,'resource'),('sos-curs3.pdf',47886,'resource'),
 ('sos-curs4.pdf',59216,'resource'),('sos-curs5.pdf',59217,'resource')])}
target=root/'.extraction/Actualisation_2026-11-12/pdf-baseline.tar'
if target.exists(): raise SystemExit('Baseline package already exists; no overwrite')
records=[]
with tarfile.open(target,'w',format=tarfile.PAX_FORMAT) as archive:
    for identity,(directory,files) in groups.items():
        for relative,module,kind in files:
            file=root/directory/relative
            data=file.read_bytes(); digest=hashlib.sha256(data).hexdigest()
            name=f'{identity}/{module}.pdf'
            archive.add(file,arcname=name,recursive=False)
            records.append({'course':identity,'key':f'module-{module}','title':file.name,'filename':file.name,
                'baseline_file':name,'sha256':digest,'pages':len(PdfReader(file).pages),'bytes':len(data),
                'source_url':f'https://curs.upb.ro/2026/mod/{kind}/view.php?id={module}'})
    import io
    payload=json.dumps({'version':1,'provenance':'Existing unchanged PC PDFs; archival timestamp is not publication time.',
                        'documents':records},ensure_ascii=False,indent=2).encode('utf-8')
    member=tarfile.TarInfo('baseline.json'); member.size=len(payload); member.mode=0o640
    archive.addfile(member,io.BytesIO(payload))
print(json.dumps({'pdfs':len(records),'pages':sum(x['pages'] for x in records),'archive_bytes':target.stat().st_size,
                  'archive':str(target)},ensure_ascii=False))
