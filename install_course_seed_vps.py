"""Install the verified initial course archive into the empty VPS master."""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import pwd
import grp
import shutil
import tarfile

ARCHIVE = Path('/tmp/cours-seed-20261112.tar')
TARGET = Path('/srv/obsidian/cours')
EXPECTED = '6ef0545bfbdddcc833369bc6ee6bdcb14ded15c92c25b21062d8e30e94cac0e6'


def sha(path):
    with path.open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def main():
    if TARGET.is_symlink() or not TARGET.is_dir() or any(TARGET.iterdir()):
        raise RuntimeError('Master must be an existing empty real directory')
    if ARCHIVE.is_symlink() or sha(ARCHIVE) != EXPECTED:
        raise RuntimeError('Archive checksum mismatch')
    uid = pwd.getpwnam('vaultagent').pw_uid
    gid = grp.getgrnam('vault').gr_gid
    with tarfile.open(ARCHIVE) as archive:
        members = archive.getmembers()
        for member in members:
            rel = PurePosixPath(member.name)
            if not member.isfile() or rel.is_absolute() or '..' in rel.parts or '\\' in member.name:
                raise RuntimeError('Unsafe TAR member')
        if len({member.name for member in members}) != len(members):
            raise RuntimeError('Duplicate archive member')
        manifest = json.load(archive.extractfile('Corpus - manifeste.json'))
        planned = {entry['path']: entry for entry in manifest['files']}
        if set(planned) | {'Corpus - manifeste.json'} != {m.name for m in members}:
            raise RuntimeError('Manifest and TAR members differ')
        for member in members:
            dest = TARGET.joinpath(*PurePosixPath(member.name).parts)
            dest.parent.mkdir(parents=True, exist_ok=True)
            with archive.extractfile(member) as source, dest.open('xb') as output:
                shutil.copyfileobj(source, output, 1024 * 1024)
            if member.name in planned:
                entry = planned[member.name]
                if dest.stat().st_size != entry['staged_size_bytes'] or sha(dest) != entry['staged_sha256']:
                    raise RuntimeError('Extracted content mismatch: ' + member.name)
    for path in [TARGET, *TARGET.rglob('*')]:
        os.chown(path, uid, gid)
        path.chmod(0o2770 if path.is_dir() else 0o660)
    print(json.dumps({'archive_sha256': EXPECTED, 'verified_files': len(planned),
                      'target': str(TARGET), 'staged_bytes': manifest['total_staged_bytes']}))


if __name__ == '__main__':
    main()
