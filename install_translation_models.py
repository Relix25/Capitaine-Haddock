"""Install only data files from the official Argos model index; no package code."""
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import urllib.request
import zipfile

index = json.load(urllib.request.urlopen('https://raw.githubusercontent.com/argosopentech/argospm-index/main/index.json',timeout=30))
root = Path('/opt/cours-watch/models')
root.mkdir(exist_ok=True)
downloads = Path('/tmp/cours-watch-models')
downloads.mkdir(exist_ok=True)
manifest = {}
for pair in [('en','fr'),('ro','en')]:
    package = next(p for p in index if (p['from_code'],p['to_code']) == pair and p['package_version'] == '1.9')
    url = package['links'][0]
    if not url.startswith('https://argos-net.com/v1/'):
        raise SystemExit('Unexpected model source')
    archive = downloads / ('_'.join(pair) + '.argosmodel')
    if not archive.exists() or not zipfile.is_zipfile(archive):
        request = urllib.request.Request(url,headers={'User-Agent':'ArgosTranslate/1.9'})
        with urllib.request.urlopen(request,timeout=60) as response, archive.open('wb') as output:
            shutil.copyfileobj(response,output)
    target = root / '_'.join(pair)
    target.mkdir(exist_ok=True)
    with zipfile.ZipFile(archive) as bundle:
        names = []
        for entry in bundle.infolist():
            path = PurePosixPath(entry.filename)
            if path.is_absolute() or '..' in path.parts or entry.file_size > 400 * 1024 * 1024:
                raise SystemExit('Invalid model archive')
            parts = path.parts[1:]
            if not parts or entry.is_dir():
                continue
            relative = PurePosixPath(*parts)
            if relative.name in ('metadata.json','sentencepiece.model','bpe.model','source.spm','target.spm','README','README.md','LICENSE') or parts[0] == 'model':
                destination = target.joinpath(*parts)
                destination.parent.mkdir(parents=True,exist_ok=True)
                with bundle.open(entry) as source, destination.open('wb') as output:
                    shutil.copyfileobj(source,output)
                destination.chmod(0o644)
                names.append(str(relative))
    if not (target / 'model/model.bin').exists() or not any((target / name).exists() for name in ('sentencepiece.model','bpe.model')):
        raise SystemExit('Expected CTranslate2 and SentencePiece assets are missing: ' + str(names))
    manifest['_'.join(pair)] = {'version':'1.9','source':url,'sha256':hashlib.sha256(archive.read_bytes()).hexdigest()}
    print(json.dumps({'installed':'_'.join(pair),'files':names,'archive_bytes':archive.stat().st_size}),flush=True)
(root / 'manifest.json').write_text(json.dumps(manifest,indent=2))
