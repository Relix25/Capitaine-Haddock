"""Inspect permissions or upload the four user-provided Haddock images to Discord."""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import time
import urllib.error
import urllib.request

from courswatch.runtime import load_discord_secrets

FILES = {'success':'haddock_joie_depot','project':'haddock_longue_vue_projet',
         'oral':'haddock_idee_oral','warning':'haddock_mille_sabords'}


def main():
    import grp

    parser = argparse.ArgumentParser()
    parser.add_argument('--check',action='store_true')
    parser.add_argument('--scope',choices=['guild','application'],default='guild')
    parser.add_argument('--assets',type=Path,default=Path('/tmp/cours-watch-haddock'))
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise SystemExit('root required for private configuration')
    config = json.loads(Path('/etc/cours-watch/config.json').read_text())['discord']
    token = load_discord_secrets()['DISCORD_BOT_TOKEN']

    def request(path,payload=None):
        data = json.dumps(payload).encode() if payload is not None else None
        for attempt in range(3):
            req = urllib.request.Request('https://discord.com/api/v10' + path,data=data,
                headers={'Authorization':'Bot ' + token,'Content-Type':'application/json',
                         'User-Agent':'UPBCourseWatch/1.0'},method='POST' if data else 'GET')
            try:
                with urllib.request.urlopen(req,timeout=30) as response:
                    return json.load(response)
            except urllib.error.HTTPError as error:
                if error.code == 429 and attempt < 2:
                    delay = float(json.loads(error.read()).get('retry_after',5))
                    if delay > 60:
                        raise SystemExit('Discord rate limit; retry later') from None
                    time.sleep(max(1,delay))
                    continue
                raise SystemExit('Discord HTTP ' + str(error.code) + '; no credential logged') from None
            except urllib.error.URLError:
                raise SystemExit('Discord unavailable; no credential logged') from None

    guild = str(config['guild_id'])
    application = str(config['application_id'])
    me = request('/users/@me')
    member = request('/guilds/' + guild + '/members/' + me['id'])
    roles = request('/guilds/' + guild + '/roles')
    member_roles = set(member['roles']) | {guild}
    permissions = 0
    for role in roles:
        if role['id'] in member_roles:
            permissions |= int(role['permissions'])
    can_create = bool(permissions & ((1 << 43) | (1 << 3)))
    existing_guild = request('/guilds/' + guild + '/emojis')
    if args.check:
        print(json.dumps({'guild_id':guild,'can_create_guild_expressions':can_create,
                          'guild_haddock_emojis':[{'id':e['id'],'name':e['name']} for e in existing_guild if e['name'] in FILES.values()]}))
        return
    if args.scope == 'guild' and not can_create:
        raise SystemExit('CREATE_GUILD_EXPRESSIONS permission is required')
    route = '/guilds/' + guild + '/emojis' if args.scope == 'guild' else '/applications/' + application + '/emojis'
    existing = existing_guild if args.scope == 'guild' else request(route)['items']
    lookup = {e['name']:e for e in existing}
    catalog = {}
    # Read/validate all four assets before creating any emoji.
    images = {}
    for key,name in FILES.items():
        blob = (args.assets / (name + '.png')).read_bytes()
        if not blob.startswith(b'\x89PNG\r\n\x1a\n') or len(blob) > 256 * 1024:
            raise SystemExit('Invalid or oversized PNG: ' + name)
        images[key] = blob
    for key,name in FILES.items():
        emoji = lookup.get(name)
        if emoji is None:
            emoji = request(route,{'name':name,'image':'data:image/png;base64,' + base64.b64encode(images[key]).decode(),
                                   **({'roles':[]} if args.scope == 'guild' else {})})
        if not emoji.get('available',True) or emoji.get('animated'):
            raise SystemExit('Unexpected emoji state: ' + name)
        catalog[key] = {'name':emoji['name'],'id':emoji['id'],'scope':args.scope,
                        'sha256':hashlib.sha256(images[key]).hexdigest()}
        print(json.dumps({'emoji':key,'name':emoji['name'],'id':emoji['id'],'scope':args.scope}),flush=True)
    output = Path('/etc/cours-watch/emojis.json')
    pending = output.with_suffix('.pending')
    pending.write_text(json.dumps(catalog,ensure_ascii=False,indent=2) + '\n')
    pending.chmod(0o640)
    os.chown(pending,0,grp.getgrnam('courswatch').gr_gid)
    os.replace(pending,output)
    print(json.dumps({'configured':len(catalog),'scope':args.scope}))


if __name__ == '__main__':
    main()
