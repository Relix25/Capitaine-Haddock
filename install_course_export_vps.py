"""Activate the reviewed course export modules without exposing secrets."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

STAGE = Path('/tmp/cours-watch-luna')
DEST = Path('/opt/cours-watch')
FILES = ('cours_watch.py', 'course_capture.py', 'course_obsidian.py',
         'moodle_pdf_versions.py', 'course_pdf_publish.py', 'publish_course_vault.py')


def atomic(path, data, mode):
    fd, name = tempfile.mkstemp(prefix='.course-install-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(name, mode)
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def main():
    config = Path('/etc/cours-watch/config.json')
    backup = Path('/var/backups/cours-watch/obsidian-20261112')
    backup.mkdir(mode=0o700, parents=True, exist_ok=True)
    backup.chmod(0o700)
    raw = config.read_bytes()
    settings = json.loads(raw)
    settings['obsidian_export'] = {'enabled': True,
        'snapshot_file': '/var/lib/cours-watch-publish/latest.json',
        'documents_dir': '/var/lib/cours-watch-publish/documents'}
    for name in (*FILES,):
        existing = DEST / name
        if existing.exists() and not (backup / name).exists():
            shutil.copyfile(existing, backup / name)
            (backup / name).chmod(0o600)
        compile((STAGE / name).read_bytes(), name, 'exec')
    if not (backup / 'config.json').exists():
        (backup / 'config.json').write_bytes(raw)
        (backup / 'config.json').chmod(0o600)
    for name in FILES:
        atomic(DEST / name, (STAGE / name).read_bytes(), 0o644)
    oldstat = config.stat()
    atomic(config, (json.dumps(settings, ensure_ascii=False, indent=2)+'\n').encode(), oldstat.st_mode & 0o777)
    os.chown(config, oldstat.st_uid, oldstat.st_gid)
    for name in ('cours-watch-obsidian.service', 'cours-watch-obsidian.path'):
        atomic(Path('/etc/systemd/system') / name, (STAGE / name).read_bytes(), 0o644)
    for name in ('cours-watch.service', 'cours-watch-bot.service'):
        dropin = Path('/etc/systemd/system') / (name + '.d')
        dropin.mkdir(exist_ok=True)
        atomic(dropin / 'obsidian-export.conf',
               b'[Service]\nReadWritePaths=/var/lib/cours-watch-publish\n', 0o644)
    atomic(DEST / 'requirements-documents.lock', b'pypdf==6.21.4\n', 0o644)
    subprocess.run(['systemctl','daemon-reload'], check=True)
    subprocess.run(['systemctl','enable','--now','cours-watch-obsidian.path'], check=True)
    print(json.dumps({'export_enabled': True, 'modules': len(FILES), 'master':'/srv/obsidian/cours'}))


if __name__ == '__main__':
    main()
