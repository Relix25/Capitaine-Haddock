"""Deploy the isolated Linktree monitor with a quiet initial baseline."""
import grp
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

STAGE=Path('/tmp/cours-esn-20261115')
DEST=Path('/opt/cours-watch')


def atomic(path,data,mode=0o644,group=None):
    fd,name=tempfile.mkstemp(prefix='.esn-install-',dir=path.parent)
    try:
        with os.fdopen(fd,'wb') as f: f.write(data)
        os.chmod(name,mode)
        if group is not None: os.chown(name,0,group)
        os.replace(name,path)
    finally: Path(name).unlink(missing_ok=True)


def main():
    if os.geteuid()!=0: raise SystemExit('Run as root')
    health=DEST/'course_health.py'
    before=health.read_bytes()
    expected='4ab00aac57b8d0c25d80fad93519dfbfd6eb4ae366e34792b55dd7496b3a3137'
    new=(STAGE/'course_health.py').read_bytes()
    if hashlib.sha256(before).hexdigest()!=expected and before!=new:
        raise SystemExit('Health module changed since review')
    backup=Path('/var/backups/cours-watch/esn-20261115')
    backup.mkdir(parents=True,mode=0o700,exist_ok=True); backup.chmod(0o700)
    if not (backup/'course_health.py').exists():
        (backup/'course_health.py').write_bytes(before); (backup/'course_health.py').chmod(0o600)
    main_path=Path('/etc/cours-watch/config.json')
    raw=main_path.read_bytes(); config=json.loads(raw)
    if not (backup/'config.json').exists():
        (backup/'config.json').write_bytes(raw); (backup/'config.json').chmod(0o600)
    group=grp.getgrnam('courswatch').gr_gid
    esn=Path('/etc/cours-watch/esn.json')
    if not esn.exists():
        atomic(esn,(json.dumps({'enabled':True,'profile':'https://linktr.ee/BucharestEvents',
            'guild_id':config['discord']['guild_id'],'channel_id':config['discord']['channel_id'],
            'role_id':None,'interval_seconds':300},indent=2)+'\n').encode(),0o640,group)
    config['esn_file']=str(esn)
    atomic(main_path,(json.dumps(config,ensure_ascii=False,indent=2)+'\n').encode(),0o640,group)
    for name in ('esn_watch.py','configure_esn.py','course_health.py'):
        compile((STAGE/name).read_bytes(),name,'exec')
        atomic(DEST/name,(STAGE/name).read_bytes())
    for name in ('cours-watch-esn.service','cours-watch-esn.timer'):
        atomic(Path('/etc/systemd/system')/name,(STAGE/name).read_bytes())
    subprocess.run(['systemctl','daemon-reload'],check=True)
    subprocess.run(['systemctl','enable','--now','cours-watch-esn.timer'],check=True)
    subprocess.run(['systemctl','start','--no-block','cours-watch-esn.service'],check=True)
    subprocess.run(['systemctl','restart','--no-block','cours-watch-bot.service'],check=True)
    print(json.dumps({'installed':True,'interval_minutes':5,'role_pending':json.loads(esn.read_text())['role_id'] is None}))


if __name__=='__main__': main()
