"""Watch the public ESN Linktree and durably deliver new links through Haddock."""
from datetime import datetime, timezone
import argparse
import asyncio
import hashlib
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import sqlite3
import tempfile
import urllib.request
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

PROFILE='https://linktr.ee/BucharestEvents'
TRACKING={'fbclid','gclid','dclid','igshid','mc_cid','mc_eid'}


def stamp(): return datetime.now(timezone.utc).isoformat(timespec='seconds')


class ParseError(ValueError): pass


class NextDataParser(HTMLParser):
    def __init__(self): super().__init__(); self.active=False; self.parts=[]
    def handle_starttag(self,tag,attrs):
        if tag=='script': self.active=dict(attrs).get('id')=='__NEXT_DATA__'
    def handle_endtag(self,tag):
        if tag=='script': self.active=False
    def handle_data(self,text):
        if self.active: self.parts.append(text)


def canonical_url(value):
    if not isinstance(value,str): raise ParseError('Invalid activity URL')
    p=urlsplit(value.strip())
    if p.scheme!='https' or not p.hostname or p.username or p.password or p.port not in (None,443):
        raise ParseError('Activity URL must be public HTTPS')
    query=[(k,v) for k,v in parse_qsl(p.query,keep_blank_values=True)
           if k.lower() not in TRACKING and not k.lower().startswith('utm_')]
    return urlunsplit(('https',p.hostname.lower(),p.path or '/',urlencode(sorted(query)),''))


def parse_page(raw):
    parser=NextDataParser(); parser.feed(raw)
    try:
        document=json.loads(''.join(parser.parts))
        props=document['props']['pageProps']
        if str(props.get('username','')).casefold()!='bucharestevents': raise ParseError('Unexpected profile')
        if props.get('hasPagePassword') or props.get('isPreview') or props.get('isDraftSession'):
            raise ParseError('Profile is locked or not public')
        links=props['links']
        if not isinstance(links,list): raise ParseError('Invalid live links list')
    except (KeyError,TypeError,json.JSONDecodeError) as exc:
        raise ParseError('Live Linktree data unavailable') from exc
    pinned=props.get('pinnedLinks')
    if isinstance(pinned,list): links=links+pinned
    found={}
    def visit(items):
        for item in items:
            if not isinstance(item,dict): raise ParseError('Invalid link entry')
            if item.get('locked') or item.get('isActive') is False or item.get('archived'):
                continue
            gates=(item.get('rules') or {}).get('gate') or {}
            if gates.get('activeOrder'): continue
            if item.get('children'): visit(item['children'])
            if not item.get('url'): continue
            identity=str(item.get('id',''))
            title=' '.join(str(item.get('title','')).split())
            if not identity or not title: raise ParseError('Link identity or title unavailable')
            try: url=canonical_url(item['url'])
            except (ValueError,ParseError): continue
            metadata=item.get('metaData') or {}
            description=str(metadata.get('ogDescription') or metadata.get('description') or '').strip()
            if description.startswith('Access Google Forms with a personal Google account'): description=''
            found.setdefault(url,{'id':identity,'title':title,'url':url,'description':description[:4000]})
    visit(links)
    return list(found.values())


async def fetch_links():
    # Anonymous ordinary Chrome: no course session, credentials, spoofing or
    # challenge bypass. HTTP and parser failures preserve the previous baseline.
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        browser=await p.chromium.launch(channel='chrome',headless=True,chromium_sandbox=True)
        try:
            context=await browser.new_context(accept_downloads=False)
            page=await context.new_page()
            # The published SSR data does not depend on optional third-party
            # scripts completing DOMContentLoaded.
            response=await page.goto(PROFILE,wait_until='commit',timeout=30000)
            if response is None or response.status!=200: raise ParseError('Linktree HTTP unavailable')
            if canonical_url(page.url)!=PROFILE: raise ParseError('Unexpected profile redirect')
            data=page.locator('script#__NEXT_DATA__')
            try:
                await data.wait_for(state='attached',timeout=20000)
            except Exception:
                print(json.dumps({'esn_browser_status':response.status,'esn_browser_title':(await page.title())[:150],
                                  'profile_data_available':False}),flush=True)
                raise
            raw='<script id="__NEXT_DATA__">'+(await data.text_content() or '')+'</script>'
            if len(raw)>4*1024*1024: raise ParseError('Linktree response exceeds limit')
            return parse_page(raw)
        finally: await browser.close()


class EventState:
    def __init__(self,path):
        self.db=sqlite3.connect(path)
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS links(id TEXT PRIMARY KEY,title TEXT NOT NULL,url TEXT NOT NULL,last_seen TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS known_urls(url TEXT PRIMARY KEY,first_seen TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS outbox(id INTEGER PRIMARY KEY,event_key TEXT UNIQUE NOT NULL,
          title TEXT NOT NULL,url TEXT NOT NULL,description TEXT NOT NULL,created TEXT NOT NULL,
          nonce TEXT NOT NULL,attempts INTEGER NOT NULL DEFAULT 0,message_id TEXT,sent_utc TEXT);
        ''')

    def ingest(self,links,now):
        initialized=self.db.execute("SELECT value FROM meta WHERE key='initialized_utc'").fetchone()
        added=0
        with self.db:
            for item in links:
                known=self.db.execute('SELECT 1 FROM known_urls WHERE url=?',(item['url'],)).fetchone()
                if initialized and not known:
                    key=hashlib.sha256((PROFILE+'|'+item['url']).encode()).hexdigest()
                    nonce=str(int(key[:16],16))
                    self.db.execute('INSERT OR IGNORE INTO outbox(event_key,title,url,description,created,nonce) VALUES(?,?,?,?,?,?)',
                        (key,item['title'],item['url'],item['description'],now,nonce))
                    added+=1
                self.db.execute('INSERT OR IGNORE INTO known_urls VALUES(?,?)',(item['url'],now))
                self.db.execute('INSERT INTO links VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE SET title=excluded.title,url=excluded.url,last_seen=excluded.last_seen',
                    (item['id'],item['title'],item['url'],now))
            if not initialized: self.db.execute("INSERT INTO meta VALUES('initialized_utc',?)",(now,))
            self.db.execute("INSERT INTO meta VALUES('last_success_utc',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(now,))
        return {'initialized_now':not bool(initialized),'new_events':added,'active_links':len(links)}

    def pending(self):
        return [dict(zip(('id','event_key','title','url','description','created','nonce','attempts'),row))
            for row in self.db.execute('SELECT id,event_key,title,url,description,created,nonce,attempts FROM outbox WHERE message_id IS NULL ORDER BY id')]

    def delivered(self,identity,message_id):
        with self.db:
            self.db.execute('UPDATE outbox SET message_id=?,sent_utc=? WHERE id=?',(str(message_id),stamp(),identity))

    def counts(self):
        pending=self.db.execute('SELECT COUNT(*) FROM outbox WHERE message_id IS NULL').fetchone()[0]
        sent,last=self.db.execute('SELECT COUNT(*),MAX(sent_utc) FROM outbox WHERE message_id IS NOT NULL').fetchone()
        return {'pending':pending,'confirmed':sent,'last_delivery_utc':last}


def safe_text(value): return str(value).replace('@','@\u200b')


def notification(event,role_id):
    if not re.fullmatch(r'\d{15,22}',str(role_id)): raise ValueError('ESN role is not configured')
    return {'content':'<@&'+str(role_id)+'> Mille sabords ! Nouvelle escale ESN.',
        'allowed_mentions':{'parse':[],'roles':[str(role_id)],'users':[]},
        'nonce':event['nonce'],'enforce_nonce':True,
        'embeds':[{'title':safe_text(event['title'])[:256],'url':event['url'],
            'description':safe_text(event['description'])[:450] or 'Une nouvelle activité vient d’apparaître sur le Linktree ESN Bucarest.',
            'color':0x00AEEF,'footer':{'text':'ESN Bucarest · Nouvelle activité · '+event['event_key'][:12]}}],
        'components':[{'type':1,'components':[
            {'type':2,'style':5,'label':'Voir l’activité','url':event['url']},
            {'type':2,'style':5,'label':'Toutes les activités','url':PROFILE}]}]}


def deliver(state,config,send):
    if not config.get('role_id'): return {'delivery':'waiting_role',**state.counts()}
    sent=0
    for event in state.pending()[:20]:
        payload=notification(event,config['role_id'])
        with state.db: state.db.execute('UPDATE outbox SET attempts=attempts+1 WHERE id=?',(event['id'],))
        receipt=send('/channels/'+str(config['channel_id'])+'/messages',payload)
        if not receipt.get('id') or str(receipt.get('channel_id'))!=str(config['channel_id']):
            raise RuntimeError('Unexpected Discord delivery receipt')
        state.delivered(event['id'],receipt['id']); sent+=1
    return {'delivery':'ok','sent_now':sent,**state.counts()}


def discover_role(config,roles):
    if config.get('role_id'): return str(config['role_id'])
    candidates=[r for r in roles if r.get('name','').casefold()=='esn' and not r.get('managed')]
    if len(candidates)!=1 or not candidates[0].get('mentionable'): return None
    return str(candidates[0]['id'])


def save_report(path,data):
    path.parent.mkdir(mode=0o700,exist_ok=True)
    fd,temp=tempfile.mkstemp(prefix='.esn-status-',dir=path.parent)
    try:
        with os.fdopen(fd,'w',encoding='utf-8') as stream: json.dump(data,stream,ensure_ascii=False,indent=2)
        os.chmod(temp,0o600); os.replace(temp,path)
    finally: Path(temp).unlink(missing_ok=True)


async def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',type=Path,default=Path('/etc/cours-watch/esn.json'))
    parser.add_argument('--data',type=Path,default=Path('/var/lib/cours-watch'))
    parser.add_argument('--dry-run',action='store_true')
    args=parser.parse_args()
    config=json.loads(args.config.read_text())
    if not config.get('enabled'): print(json.dumps({'status':'disabled'})); return 0
    import fcntl
    with (args.data/'esn.lock').open('a') as lock:
        try: fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            print(json.dumps({'status':'busy','action':'skipped'})); return 0
        previous={}
        path=args.data/'reports/esn-status.json'
        if path.exists(): previous=json.loads(path.read_text())
        report={**previous,'checked_utc':stamp(),'profile':PROFILE,'interval_seconds':300,
            'role_id':config.get('role_id'),'channel_id':config['channel_id']}
        state=None
        try:
            links=await fetch_links()
            if args.dry_run:
                print(json.dumps({'status':'dry_run','active_links':len(links),'links':links},ensure_ascii=False)); return 0
            state=EventState(args.data/'esn.db')
            report.update(state.ingest(links,report['checked_utc']),status='ok',last_success_utc=stamp(),error_type=None)
            from courswatch.discord_app.transport import discord_request,load_discord_secrets
            token=load_discord_secrets()['DISCORD_BOT_TOKEN']
            if not config.get('role_id'):
                request=urllib.request.Request('https://discord.com/api/v10/guilds/'+str(config['guild_id'])+'/roles',
                    headers={'Authorization':'Bot '+token,'User-Agent':'UPBCourseWatch/1.0'})
                with urllib.request.urlopen(request,timeout=20) as response: roles=json.load(response)
                config['role_id']=discover_role(config,roles)
            report['role_id']=config.get('role_id')
            report.update(deliver(state,config,lambda path,payload:discord_request('https://discord.com/api/v10'+path,payload,token)))
        except Exception as error:
            if args.dry_run: raise
            report.update(status='error',error_type=type(error).__name__)
            if state: report.update(state.counts())
        finally:
            if state: state.db.close()
        save_report(path,report)
        print(json.dumps(report,ensure_ascii=False))
        return 0 if report['status']=='ok' else 1


if __name__=='__main__': raise SystemExit(asyncio.run(main()))
