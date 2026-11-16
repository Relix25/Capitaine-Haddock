"""Export code and systemd units only; never read configuration or runtime data."""
import hashlib
import json
from pathlib import Path
import tarfile


def main():
    root = Path('/opt/cours-watch')
    files = [(path,Path(path.name)) for path in sorted(root.iterdir())
             if path.is_file() and not path.is_symlink()
             and (path.suffix in {'.py','.sh','.txt','.lock'} or path.name == 'README.md')]
    assets = root / 'assets'
    if assets.exists():
        for path in sorted(assets.rglob('*')):
            if path.is_file() and not path.is_symlink() and path.suffix in {'.png','.ogg','.json'}:
                files.append((path,path.relative_to(root)))
    units = Path('/etc/systemd/system')
    for path in sorted(units.glob('cours-watch*')):
        if path.is_file() and not path.is_symlink() and path.suffix in {'.service','.timer','.path'}:
            files.append((path,Path('systemd') / path.name))
        elif path.is_dir() and path.name.endswith('.d'):
            for conf in sorted(path.glob('*.conf')):
                if not conf.is_symlink():
                    files.append((conf,Path('systemd') / path.name / conf.name))
    manifest = []
    output = Path('/tmp/capitaine-source-20261117.tar.gz')
    with tarfile.open(output,'w:gz') as archive:
        for path,name in files:
            data = path.read_bytes()
            if len(data) > 2 * 1024 * 1024:
                raise SystemExit('Unexpected source file size')
            if any(part in {'.env','secrets.json','browser-profile','models','reports'} for part in name.parts):
                raise SystemExit('Unexpected source path')
            archive.add(path,arcname=str(name),recursive=False)
            manifest.append({'path':name.as_posix(),'sha256':hashlib.sha256(data).hexdigest()})
    output.chmod(0o644)
    report = Path('/tmp/capitaine-source-20261117-manifest.json')
    report.write_text(json.dumps(manifest,indent=2))
    report.chmod(0o644)
    print(json.dumps({'source_files':len(files),'archive_bytes':output.stat().st_size,
                      'sha256':hashlib.sha256(output.read_bytes()).hexdigest()}))


if __name__ == '__main__':
    main()
