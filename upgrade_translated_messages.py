"""Edit our existing teacher-message notifications using cached French translations."""
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import time
import urllib.error
import urllib.request

from cours_watch import discord_request, load_discord_secrets
from course_translation import eligible, with_cached_translation
from discord_presentation import embed_payload


def main():
    if os.geteuid() != 0:
        raise SystemExit('root required for private configuration')
    cfg = json.loads(Path('/etc/cours-watch/config.json').read_text())['discord']
    secrets = load_discord_secrets()
    token = secrets['DISCORD_BOT_TOKEN']
    data = Path('/var/lib/cours-watch')
    with closing(sqlite3.connect('file:' + str(data / 'state.db') + '?mode=ro',uri=True)) as db:
        rows = db.execute('''SELECT o.payload,r.destination,r.message_id,r.channel_id FROM outbox o
            JOIN discord_receipts r ON r.event_id=o.id WHERE r.channel_id=? ORDER BY o.id''',(str(cfg['channel_id']),)).fetchall()
    edited,missing = [],[]
    for payload,destination,message_id,channel_id in rows:
        event = json.loads(payload)
        if not eligible(event):
            continue
        event = with_cached_translation(event,data)
        if event.get('translation',{}).get('status') != 'translated':
            raise SystemExit('A historical translation is not ready; stopped')
        for attempt in range(3):
            request = urllib.request.Request('https://discord.com/api/v10/channels/' + channel_id + '/messages/' + message_id,
                headers={'Authorization':'Bot ' + token,'User-Agent':'UPBCourseWatch/1.0'})
            try:
                with urllib.request.urlopen(request,timeout=20) as response:
                    previous = json.load(response)
                break
            except urllib.error.HTTPError as error:
                if error.code == 404:
                    previous = None
                    missing.append(message_id)
                    break
                if error.code == 429 and attempt < 2:
                    delay = max(1,float(json.loads(error.read()).get('retry_after',5)))
                    if delay <= 25:
                        time.sleep(delay)
                        continue
                raise SystemExit('Discord HTTP ' + str(error.code) + '; no credential logged') from None
            except urllib.error.URLError:
                raise SystemExit('Discord unavailable; no credential logged') from None
        if previous is None:
            continue
        webhook = destination == 'webhook'
        own = (previous.get('webhook_id') == secrets['DISCORD_WEBHOOK_URL'].rstrip('/').split('/')[-2] if webhook
               else str(previous.get('author',{}).get('id')) == str(cfg['application_id']))
        if not own:
            raise SystemExit('Historical message ownership check failed')
        body = embed_payload(event,interactive=not webhook)
        body['allowed_mentions'] = {'parse':[],'users':[],'roles':[],'replied_user':False}
        endpoint = (secrets['DISCORD_WEBHOOK_URL'].rstrip('/') + '/messages/' + message_id if webhook else
                    'https://discord.com/api/v10/channels/' + channel_id + '/messages/' + message_id)
        reply = discord_request(endpoint,body,None if webhook else token,method='PATCH')
        if str(reply.get('id')) != message_id or not reply.get('embeds'):
            raise SystemExit('Unexpected edit result')
        edited.append({'message_id':message_id,'course':event['course'],'interactive':not webhook,'language':'fr'})
    proof = {'edited':edited,'deleted_messages_skipped':missing,'new_role_ping':False}
    path = Path('/tmp/cours-watch-translations-proof.json')
    path.write_text(json.dumps(proof,ensure_ascii=False,indent=2))
    path.chmod(0o644)
    print(json.dumps(proof,ensure_ascii=False,indent=2))


if __name__ == '__main__':
    main()
