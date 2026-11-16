"""Enable the user's chosen 50% voice mode only after a real native-voice receipt."""
import grp
import json
import os
from pathlib import Path

from haddock_voice import load_samples

if os.geteuid() != 0:
    raise SystemExit('root required')
samples = load_samples()
proof = json.loads(Path('/etc/cours-watch/voice-demo.json').read_text())
path = Path('/etc/cours-watch/config.json')
original = path.read_bytes()
config = json.loads(original)
if (len(samples) != 5 or not proof.get('native_voice') or not proof.get('waveform_present')
        or proof['channel_id'] != config['discord']['channel_id']):
    raise SystemExit('Five verified assets and a real native voice receipt required')
egg = config['discord']['easter_egg']
if not egg.get('enabled'):
    raise SystemExit('Existing authorized easter egg must be enabled')
egg['voice_probability'] = 0.5
if path.read_bytes() != original:
    raise SystemExit('Concurrent configuration change; stopped')
pending = path.with_suffix('.pending')
pending.write_text(json.dumps(config,ensure_ascii=False,indent=2) + '\n')
pending.chmod(0o640)
os.chown(pending,0,grp.getgrnam('courswatch').gr_gid)
os.replace(pending,path)
print(json.dumps({'voice_probability':0.5,'samples':len(samples),'cooldown_seconds':egg['cooldown_seconds'],
                  'demo_message_id':proof['message_id'],'new_permissions_added':False}))
