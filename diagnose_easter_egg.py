"""Read only recent matching messages; output only routing/role diagnostic metadata."""
import json
from pathlib import Path
import urllib.request
from cours_watch import load_discord_secrets
from haddock_easter_egg import triggered

config = json.loads(Path('/etc/cours-watch/config.json').read_text())['discord']
token = load_discord_secrets()['DISCORD_BOT_TOKEN']

def get(path):
    request = urllib.request.Request('https://discord.com/api/v10' + path,
        headers={'Authorization':'Bot ' + token,'User-Agent':'UPBCourseWatch/1.0'})
    try:
        with urllib.request.urlopen(request,timeout=20) as response:
            return json.load(response)
    except Exception:
        raise SystemExit('Discord diagnosis failed; no credential logged') from None

guild = get('/guilds/' + config['guild_id'])
messages = get('/channels/' + config['channel_id'] + '/messages?limit=30')
matches = []
for message in messages:
    if message['author'].get('bot') or message.get('webhook_id') or not triggered(message.get('content','')):
        continue
    member = get('/guilds/' + config['guild_id'] + '/members/' + message['author']['id'])
    matches.append({'message_id':message['id'],'created':message['timestamp'],
                    'has_upb_role':bool(set(member['roles']) & set(config['role_ids'])),
                    'server_owner':message['author']['id'] == guild['owner_id']})
print(json.dumps({'configured_channel_id':config['channel_id'],'recent_human_trigger_messages':matches},ensure_ascii=False,indent=2))
