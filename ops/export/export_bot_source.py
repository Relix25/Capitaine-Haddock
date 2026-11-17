"""Export code and systemd units only; never read configuration or runtime data."""
import hashlib
import io
import json
from pathlib import Path
import tarfile
from ops.build_release import PACKAGE_DATA


def main():
    root = Path('/opt/cours-watch')
    files = [(path,Path(path.name)) for path in sorted(root.iterdir())
             if path.is_file() and not path.is_symlink()
             and (path.suffix in {'.py','.sh','.txt','.lock'} or path.name == 'README.md')]
    for package in ('courswatch', 'ops', 'tools', 'tests'):
        folder = root / package
        if folder.is_symlink():
            raise SystemExit('Symlinked source package')
        if folder.exists():
            package_files = set(folder.rglob('*.py'))
            package_files.update(root / name for name in PACKAGE_DATA
                                 if (root / name).is_relative_to(folder))
            for path in sorted(package_files):
                if (path.is_symlink() or any(parent.is_symlink() for parent in path.parents
                                             if parent != root and parent.is_relative_to(root))):
                    raise SystemExit('Symlinked source file')
                if path.is_file() and '__pycache__' not in path.parts:
                    files.append((path, path.relative_to(root)))
    assets = root / 'assets'
    if assets.exists():
        for path in sorted(assets.rglob('*')):
            if path.is_file() and not path.is_symlink() and path.suffix in {'.png','.ogg','.json'}:
                files.append((path,path.relative_to(root)))
    # Only synthetic multimodal fixtures belong to code exports; never caches.
    fixture_dir = root / 'tests/fixtures/media'
    if fixture_dir.is_symlink():
        raise SystemExit('Symlinked fixture directory')
    if fixture_dir.exists():
        for path in sorted(fixture_dir.rglob('*')):
            if path.is_file() and path.suffix in {'.png','.jpg','.jpeg','.gif','.webp','.json'}:
                if path.is_symlink() or any(parent.is_symlink() for parent in path.parents
                                             if parent != root and parent.is_relative_to(root)):
                    raise SystemExit('Symlinked fixture source')
                files.append((path, path.relative_to(root)))
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
            info = archive.gettarinfo(str(path), arcname=name.as_posix())
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
            manifest.append({'path':name.as_posix(),'sha256':hashlib.sha256(data).hexdigest()})
    output.chmod(0o644)
    report = Path('/tmp/capitaine-source-20261117-manifest.json')
    report.write_text(json.dumps(manifest,indent=2))
    report.chmod(0o644)
    print(json.dumps({'source_files':len(files),'archive_bytes':output.stat().st_size,
                      'sha256':hashlib.sha256(output.read_bytes()).hexdigest()}))


if __name__ == '__main__':
    main()
