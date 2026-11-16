"""Update existing embeds among our eight course cards; preserve content and suppress mentions."""
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import time
import urllib.error
import urllib.request

from cours_watch import discord_request, load_discord_secrets
from discord_presentation import embed_payload
from haddock_emojis import catalog


def main():
    if os.geteuid() != 0:
        raise SystemExit('root required for private configuration')
    config = json.loads(Path('/etc/cours-watch/config.json').read_text())['discord']
    secrets = load_discord_secrets()
    token = secrets['DISCORD_BOT_TOKEN']
    emojis = catalog()
    if len(emojis) != 4 or any(e.get('scope') != 'guild' for e in emojis.values()):
        raise SystemExit('Four guild emojis must be configured first')

    def get(path):
        for attempt in range(3):
            request = urllib.request.Request('https://discord.com/api/v10' + path,
                headers={'Authorization':'Bot ' + token,'User-Agent':'UPBCourseWatch/1.0'})
            try:
                with urllib.request.urlopen(request,timeout=20) as response:
                    return json.load(response)
            except urllib.error.HTTPError as error:
                if error.code == 429 and attempt < 2:
                    delay = max(1,float(json.loads(error.read()).get('retry_after',5)))
                    if delay <= 25:
                        time.sleep(delay)
                        continue
                raise SystemExit(json.dumps({'error':'Discord verification failed','http_status':error.code,'resource':path})) from None
            except Exception:
                raise SystemExit('Discord verification failed; no credential logged') from None

    live = {e['id']:e for e in get('/guilds/' + config['guild_id'] + '/emojis')}
    if any(e['id'] not in live or not live[e['id']].get('available',True) for e in emojis.values()):
        raise SystemExit('Configured guild emoji unavailable')
    with closing(sqlite3.connect('file:/var/lib/cours-watch/state.db?mode=ro',uri=True)) as db:
        rows = db.execute('''SELECT o.event_key,o.payload,r.destination,r.message_id,r.channel_id
            FROM outbox o JOIN discord_receipts r ON r.event_id=o.id
            WHERE (o.event_key LIKE 'initial-brief:2026-11-12:%' OR o.event_key LIKE 'interactive-demo:%')
            AND r.channel_id=? ORDER BY o.id''',(str(config['channel_id']),)).fetchall()
    if len(rows) != 8:
        raise SystemExit('Expected exactly the eight existing course cards; stopped')
    plan = []
    skipped = 0
    for key,event,destination,message_id,channel_id in rows:
        message = get('/channels/' + channel_id + '/messages/' + message_id)
        is_webhook = destination == 'webhook'
        own = (message.get('webhook_id') == secrets['DISCORD_WEBHOOK_URL'].rstrip('/').split('/')[-2]
               if is_webhook else str(message.get('author',{}).get('id')) == str(config['application_id']))
        if not own:
            raise SystemExit(json.dumps({'error':'Existing card ownership or embed check failed',
                'key':key,'destination':destination,'message_id':message_id,
                'author_id':message.get('author',{}).get('id'),'webhook_id':message.get('webhook_id'),
                'embeds':len(message.get('embeds',[]))}))
        if not message.get('embeds'):
            skipped += 1
            continue
        url = (secrets['DISCORD_WEBHOOK_URL'].rstrip('/') + '/messages/' + message_id if is_webhook
               else 'https://discord.com/api/v10/channels/' + channel_id + '/messages/' + message_id)
        body = embed_payload(json.loads(event),interactive=not is_webhook)
        body.pop('content',None)
        body['allowed_mentions'] = {'parse':[],'roles':[],'users':[],'replied_user':False}
        plan.append((url,body,None if is_webhook else token,message_id,message.get('content','')))
    if not plan:
        raise SystemExit('No existing embeds to update')
    edited = []
    for url,body,authorization,message_id,old_content in plan:
        reply = discord_request(url,body,authorization,method='PATCH')
        if (str(reply.get('id')) != message_id or reply.get('content','') != old_content
                or '<:haddock_' not in json.dumps(reply.get('embeds',[]))):
            raise SystemExit('Unexpected updated card; stopped')
        edited.append({'message_id':message_id,'custom_emoji':True,'content_preserved':True,
                       'interactive':bool(reply.get('components'))})
    proof = {'guild_emojis':[{key:value[key] for key in ('name','id')} for value in emojis.values()],
             'updated_cards':edited,'legacy_messages_without_embeds':skipped,
             'new_message_posted':False,'mention_parsing':False}
    path = Path('/tmp/cours-watch-haddock-proof.json')
    path.write_text(json.dumps(proof,ensure_ascii=False,indent=2))
    path.chmod(0o644)
    print(json.dumps(proof,ensure_ascii=False,indent=2))


if __name__ == '__main__':
    main()
