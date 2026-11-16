"""Enable the documented Discord Message Content flag and the scoped Haddock feature."""
import grp
import json
import os
from pathlib import Path
import urllib.request

from cours_watch import discord_request, load_discord_secrets

if os.geteuid() != 0:
    raise SystemExit('root required')
path = Path('/etc/cours-watch/config.json')
original = path.read_bytes()
config = json.loads(original)
token = load_discord_secrets()['DISCORD_BOT_TOKEN']
url = 'https://discord.com/api/v10/applications/@me'
request = urllib.request.Request(url,headers={'Authorization':'Bot ' + token,'User-Agent':'UPBCourseWatch/1.0'})
try:
    with urllib.request.urlopen(request,timeout=20) as response:
        application = json.load(response)
except Exception:
    raise SystemExit('Application check failed; no credential logged') from None
if application['id'] != str(config['discord']['application_id']):
    raise SystemExit('Unexpected application identity')
flags = int(application.get('flags_new',application.get('flags',0)))
if not flags & ((1 << 18) | (1 << 19)):
    application = discord_request(url,{'flags':flags | (1 << 19)},token,method='PATCH')
    flags = int(application.get('flags_new',application.get('flags',0)))
if not flags & ((1 << 18) | (1 << 19)):
    raise SystemExit('Enable Message Content Intent in Discord Developer Portal')
if path.read_bytes() != original:
    raise SystemExit('Concurrent configuration change; stopped before writing')
config['discord']['easter_egg'] = {'enabled':True,'cooldown_seconds':30}
pending = path.with_suffix('.pending')
pending.write_text(json.dumps(config,ensure_ascii=False,indent=2) + '\n')
pending.chmod(0o640)
os.chown(pending,0,grp.getgrnam('courswatch').gr_gid)
os.replace(pending,path)
print(json.dumps({'application_id':application['id'],'message_content_allowed':True,
                  'easter_egg_enabled':True,'cooldown_seconds':30,'guild_and_channel_scope_preserved':True}))
