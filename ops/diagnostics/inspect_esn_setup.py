"""Read public Linktree structure and the bot's non-secret role capabilities."""
import json
import os
from pathlib import Path
import sys
import urllib.request
from html.parser import HTMLParser

from courswatch.runtime import load_discord_secrets


class Scripts(HTMLParser):
    def __init__(self): super().__init__(); self.active=False; self.parts=[]
    def handle_starttag(self,tag,attrs):
        self.active=tag=='script' and dict(attrs).get('id')=='__NEXT_DATA__'
    def handle_endtag(self,tag):
        if tag=='script': self.active=False
    def handle_data(self,data):
        if self.active: self.parts.append(data)


def main():
    config=json.loads(Path('/etc/cours-watch/config.json').read_text())['discord']
    token=load_discord_secrets()['DISCORD_BOT_TOKEN']
    def get(path):
        req=urllib.request.Request('https://discord.com/api/v10'+path,
             headers={'Authorization':'Bot '+token,'User-Agent':'UPBCourseWatch/1.0'})
        with urllib.request.urlopen(req,timeout=20) as response: return json.load(response)
    guild=str(config['guild_id'])
    me=get('/users/@me')
    member=get('/guilds/'+guild+'/members/'+me['id'])
    roles=get('/guilds/'+guild+'/roles')
    mask=0
    for role in roles:
        if role['id'] in {*member['roles'],guild}: mask|=int(role['permissions'])
    channel=get('/channels/'+str(config['channel_id']))
    print(json.dumps({'guild_id':guild,'channel_id':str(config['channel_id']),
        'channel_name':channel['name'],'can_manage_roles':bool(mask&((1<<3)|(1<<28))),
        'bot_highest_role':max((r['position'] for r in roles if r['id'] in member['roles']),default=0),
        'esn_roles':[{'id':r['id'],'name':r['name'],'position':r['position'],
                      'mentionable':r['mentionable']} for r in roles if r['name'].casefold()=='esn']},ensure_ascii=False))
    req=urllib.request.Request('https://linktr.ee/BucharestEvents',headers={'User-Agent':'UPBEventsWatch/1.0','Cache-Control':'no-cache'})
    with urllib.request.urlopen(req,timeout=30) as response:
        raw=response.read(4*1024*1024)
        print(json.dumps({'linktree_status':response.status,'bytes':len(raw),'content_type':response.headers.get('Content-Type')}))
    path=Path('/tmp/esn-linktree-snapshot.html')
    path.write_bytes(raw); path.chmod(0o600)
    if os.geteuid()==0:
        import pwd
        os.chown(path,pwd.getpwnam('ubuntu').pw_uid,-1)
    parser=Scripts(); parser.feed(raw.decode('utf-8'))
    data=json.loads(''.join(parser.parts))
    props=data.get('props',{}).get('pageProps',{})
    print(json.dumps({'page_prop_keys':list(props),'account_keys':list(props.get('account',{}))},ensure_ascii=False))
    links=props.get('links')
    if links is not None:
        print(json.dumps({'links':[dict(item) for item in links]},ensure_ascii=False))


if __name__=='__main__': main()
