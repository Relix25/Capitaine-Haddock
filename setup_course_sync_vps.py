"""Configure only the course master share, preserving existing server shares."""
from pathlib import Path
import pwd
import grp
import shutil
import subprocess

PC = 'PFUYAJJ-UBDGI6D-PVT54BH-7YEQBUO-PPETOFB-HXOK4ID-5S2HIYI-VGBQVQX'
SERVER = 'NXLAOUH-M3TZAVT-T5L6ZX4-U46AIY4-2JTGH2E-GLVDAJH-PR4MDMN-FN3HIAN'


def main():
    target = Path('/srv/obsidian/cours/.stignore')
    expected = b'/.git\n/.obsidian\n/.extraction\n/Outils\n'
    if target.exists() and target.read_bytes() != expected:
        raise RuntimeError('Unexpected existing ignore rules')
    if not target.exists():
        with target.open('xb') as f:
            f.write(expected)
        target.chmod(0o660)
        import os
        os.chown(target, pwd.getpwnam('vaultagent').pw_uid, grp.getgrnam('vault').gr_gid)
    helper = Path('/tmp/cours-watch-docs-luna/configure_course_sync.py')
    shutil.copyfile('/tmp/cours-watch-luna/configure_course_sync.py', helper)
    helper.chmod(0o644)
    subprocess.run(['systemctl','stop','syncthing@vaultsync.service'], check=True)
    try:
        subprocess.run(['sudo','-u','vaultsync','python3',str(helper),
            '--role','server','--config','/srv/obsidian/.local/state/syncthing/config.xml',
            '--vault','/srv/obsidian/cours','--pc-id',PC,'--server-id',SERVER,'--apply'], check=True)
    finally:
        subprocess.run(['systemctl','start','syncthing@vaultsync.service'], check=True)


if __name__ == '__main__':
    main()
