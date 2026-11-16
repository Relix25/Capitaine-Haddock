"""Enable the user-authorized Luna mode after the real API smoke test passes."""
import json
import os
from pathlib import Path
import pwd
import grp

config = Path('/etc/cours-watch/config.json')
report = json.loads(Path('/var/lib/cours-watch/reports/luna-status.json').read_text())
if report.get('status') != 'ready' or report.get('model') != 'gpt-6-luna' or report.get('calls',0) < 3:
    raise SystemExit('Successful real Luna smoke test required')
original = config.read_bytes()
value = json.loads(original)
value['discord']['luna'] = {'enabled':True,'model':'gpt-6-luna','daily_token_limit':1_000_000}
backup = config.with_name('config.before-luna.json')
if not backup.exists():
    backup.write_bytes(original)
    backup.chmod(0o640)
    os.chown(backup,0,grp.getgrnam('courswatch').gr_gid)
pending = config.with_suffix('.pending')
pending.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
pending.chmod(0o640)
os.chown(pending,0,grp.getgrnam('courswatch').gr_gid)
if config.read_bytes() != original:
    raise SystemExit('Concurrent config change; activation cancelled')
os.replace(pending,config)
print(json.dumps({'luna_enabled':True,'model':'gpt-6-luna','daily_token_limit':1_000_000,'reset':'00:00 Europe/Bucharest'}))
