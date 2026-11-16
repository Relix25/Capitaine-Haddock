"""Convert only the four own initial webhook messages to compact embeds, without pinging again."""
import grp
import json
import os
from pathlib import Path
import sqlite3

from cours_watch import discord_request
from discord_presentation import embed_payload

if os.geteuid() != 0:
    raise SystemExit('root required')
folder = Path('/etc/cours-watch')
config = json.loads((folder / 'config.json').read_text())
secrets = json.loads((folder / 'secrets.json').read_text())
webhook = secrets.get('DISCORD_WEBHOOK_URL','')
db = sqlite3.connect('/var/lib/cours-watch/state.db')
rows = db.execute('''SELECT o.payload,r.message_id,r.channel_id FROM outbox o
 JOIN discord_receipts r ON r.event_id=o.id WHERE o.event_key LIKE ? AND r.destination=? ORDER BY o.id''',
 ('initial-brief:2026-11-12:%','webhook')).fetchall()
if len(rows) != 4 or any(str(row[2]) != str(config['discord']['channel_id']) for row in rows):
    raise SystemExit('Expected four own initial messages in the configured channel')
receipts = []
for payload,message_id,channel_id in rows:
    body = embed_payload(json.loads(payload),interactive=False)
    body['allowed_mentions'] = {'parse':[],'users':[],'roles':[]}
    response = discord_request(webhook + '/messages/' + message_id,body,method='PATCH')
    if str(response.get('id')) != message_id or len(response.get('embeds',[])) != 1:
        raise SystemExit('Unexpected edit receipt')
    receipts.append({'message_id':message_id,'embeds':len(response['embeds']),'new_role_pings':response.get('mention_roles',[])})
config['discord']['presentation'] = 'embed'
pending = folder / 'config.json.pending'
pending.write_text(json.dumps(config,ensure_ascii=False,indent=2) + '\n')
pending.chmod(0o640)
os.chown(pending,0,grp.getgrnam('courswatch').gr_gid)
os.replace(pending,folder / 'config.json')
print(json.dumps({'updated_messages':receipts,'future_presentation':'embed'},ensure_ascii=False))
