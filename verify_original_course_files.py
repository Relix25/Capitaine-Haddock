"""Verify that the thirty originally collected course files remain byte-identical."""
import hashlib
import json
from pathlib import Path

root=Path(__file__).resolve().parents[2]
records=json.loads((root/'.extraction/sources_originales.json').read_text(encoding='utf-8-sig'))
failed=[]
for record in records:
    file=Path(record['Path'])
    if not file.resolve().is_relative_to(root): raise SystemExit('Unexpected source path')
    digest=hashlib.sha256()
    with file.open('rb') as stream:
        while chunk:=stream.read(1024*1024): digest.update(chunk)
    if digest.hexdigest().lower()!=record['SHA256'].lower() or file.stat().st_size!=record['Bytes']:
        failed.append(str(file.relative_to(root)))
print(json.dumps({'originals_checked':len(records),'unchanged':not failed,'differences':failed},ensure_ascii=False))
raise SystemExit(bool(failed))
