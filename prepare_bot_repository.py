"""Prepare the first repository from local tooling and a verified code-only VPS export."""
import hashlib
import json
from pathlib import Path
import re
import shutil
import tarfile

ROOT = Path(__file__).resolve().parents[2]
SOURCE = Path(__file__).resolve().parent
DEST = ROOT / 'Outils/Capitaine-Haddock'


def main():
    archive = ROOT / '.extraction/capitaine-source-20261117.tar.gz'
    if hashlib.sha256(archive.read_bytes()).hexdigest() != '83dab358e2bd3c82d9ca871308a8645231ec73d84fd9cd908ecc705cfef05275':
        raise SystemExit('Unexpected VPS export')
    DEST.mkdir(exist_ok=False)
    (DEST / 'docs').mkdir()
    (DEST / 'examples').mkdir()
    for path in SOURCE.iterdir():
        if path.is_file() and path.suffix in {'.py','.sh','.service','.timer','.path','.txt','.lock'}:
            shutil.copy2(path,DEST / path.name)
    shutil.copytree(SOURCE / 'assets',DEST / 'assets')
    for name in ('config.json','reminders.json','translation.json'):
        shutil.copy2(SOURCE / name,DEST / name)
    shutil.copy2(SOURCE / 'README.md',DEST / 'docs/Operations-historique-2026-11-12.md')
    with tarfile.open(archive,'r:gz') as bundle:
        for member in bundle.getmembers():
            relative = Path(member.name)
            if member.isdir():
                continue
            if not member.isfile() or relative.is_absolute() or '..' in relative.parts:
                raise SystemExit('Unsafe archive member')
            target = DEST / ('docs/README-source-VPS.md' if member.name == 'README.md' else member.name)
            if not target.resolve().is_relative_to(DEST.resolve()):
                raise SystemExit('Unsafe target')
            target.parent.mkdir(parents=True,exist_ok=True)
            target.write_bytes(bundle.extractfile(member).read())
    shutil.copy2(ROOT / '.extraction/capitaine-source-20261117-manifest.json',DEST / 'docs/source-VPS-SHA256.json')
    # Code-only scan: emit locations, never the matched secret-like contents.
    rules = {
        'api_key':r'sk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{24,}',
        'webhook':r'https://(?:canary\.|ptb\.)?discord(?:app)?\.com/api/webhooks/\d+/[A-Za-z0-9_-]{25,}',
        'discord_token':r'[MN][A-Za-z0-9_-]{23,}\.[A-Za-z0-9_-]{6}\.[A-Za-z0-9_-]{27,}',
        'private_key':r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----',
    }
    findings = []
    for path in DEST.rglob('*'):
        if not path.is_file() or path.suffix in {'.png','.ogg'}:
            continue
        content = path.read_text(encoding='utf-8')
        for rule,pattern in rules.items():
            for match in re.finditer(pattern,content):
                findings.append({'path':str(path.relative_to(DEST)),'rule':rule,
                                 'line':content[:match.start()].count('\n') + 1})
    print(json.dumps({'prepared':str(DEST),'files':sum(p.is_file() for p in DEST.rglob('*')),
                      'secret_pattern_findings':findings},ensure_ascii=False))
    if findings:
        raise SystemExit('Review required before Git staging')


if __name__ == '__main__':
    main()
