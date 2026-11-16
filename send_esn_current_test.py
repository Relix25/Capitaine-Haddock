"""Send the user-requested current-Linktree test, with one ESN ping per run."""
import argparse
import asyncio
import fcntl
import hashlib
import json
from pathlib import Path
import re
import urllib.error
import urllib.request

from cours_watch import discord_request,load_discord_secrets,stamp
from esn_watch import PROFILE,fetch_links,safe_text,save_report


async def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--test-id',required=True)
    args=parser.parse_args()
    if not re.fullmatch(r'[a-z0-9-]{1,70}',args.test_id): raise SystemExit('Invalid test identifier')
    data=Path('/var/lib/cours-watch')
    config=json.loads(Path('/etc/cours-watch/esn.json').read_text())
    if (str(config.get('guild_id'))!='1376984512046563519'
            or str(config.get('channel_id'))!='1570209088929800282'
            or str(config.get('role_id'))!='1571515589501915308'):
        raise SystemExit('Expected Campus Campus / UPB / ESN destination required')
    token=load_discord_secrets()['DISCORD_BOT_TOKEN']
    def get(route):
        req=urllib.request.Request('https://discord.com/api/v10'+route,
            headers={'Authorization':'Bot '+token,'User-Agent':'UPBCourseWatch/1.0'})
        try:
            with urllib.request.urlopen(req,timeout=20) as response: return json.load(response)
        except urllib.error.HTTPError as error:
            raise RuntimeError('Discord HTTP '+str(error.code)) from None
    receipt_path=data/'reports'/('esn-current-links-test-'+args.test_id+'.json')
    with (data/'esn-current-test.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        if receipt_path.exists():
            previous=json.loads(receipt_path.read_text())
            print(json.dumps({'already_attempted':True,**previous},ensure_ascii=False))
            return 0 if previous.get('status')=='confirmed' else 2
        channel=get('/channels/'+str(config['channel_id']))
        if str(channel.get('guild_id'))!=str(config['guild_id']): raise SystemExit('Wrong guild')
        roles=get('/guilds/'+str(config['guild_id'])+'/roles')
        role=next((r for r in roles if r['id']==str(config['role_id'])),None)
        if not role or role['name']!='ESN' or not role['mentionable']:
            raise SystemExit('The ESN notification role is not ready')
        links=await fetch_links()
        if not links: raise SystemExit('No live links; no empty test posted')
        checked=stamp()
        report={'status':'sending','test_id':args.test_id,'source':PROFILE,
            'checked_utc':checked,'guild_id':str(config['guild_id']),
            'channel_id':str(config['channel_id']),'role_id':role['id'],
            'active_links':len(links),'links':[{'title':i['title'],'url':i['url']} for i in links],
            'messages':[]}
        save_report(receipt_path,report)
        for offset in range(0,len(links),10):
            batch=links[offset:offset+10]
            first=offset==0
            role_id=str(config['role_id'])
            content=(('<@&'+role_id+'> ') if first else '')
            content+='Mille sabords ! **TEST — liens actuellement affichés sur le Linktree ESN.**\nCes cartes sont un essai, pas de nouvelles activités détectées.'
            embeds=[]
            for index,item in enumerate(batch,offset+1):
                embeds.append({'title':safe_text(item['title'])[:200],'url':item['url'],
                    'description':safe_text(item['description'])[:300] or 'Lien actuellement publié sur le Linktree ESN Bucarest.',
                    'color':0x00AEEF,'footer':{'text':f'TEST · lien actuel {index}/{len(links)}'}})
            buttons=[{'type':2,'style':5,'label':('Voir '+safe_text(i['title']))[:70],'url':i['url']} for i in batch]
            components=[{'type':1,'components':buttons[pos:pos+5]} for pos in range(0,len(buttons),5)]
            nonce=str(int(hashlib.sha256((args.test_id+':'+str(offset)).encode()).hexdigest()[:16],16))
            payload={'content':content,'embeds':embeds,'components':components,
                'nonce':nonce,'enforce_nonce':True,
                'allowed_mentions':{'parse':[],'users':[],'roles':[role_id] if first else []}}
            sent=discord_request('https://discord.com/api/v10/channels/'+str(config['channel_id'])+'/messages',payload,token)
            if not sent.get('id') or str(sent.get('channel_id'))!=str(config['channel_id']):
                raise RuntimeError('Unexpected Discord receipt')
            readback=get('/channels/'+str(config['channel_id'])+'/messages/'+str(sent['id']))
            if len(readback.get('embeds',[]))!=len(batch): raise RuntimeError('Embed count mismatch')
            if {e.get('url') for e in readback['embeds']}!={i['url'] for i in batch}: raise RuntimeError('Activity links mismatch')
            if readback.get('mention_everyone'): raise RuntimeError('Unexpected everyone mention')
            if set(readback.get('mention_roles',[]))!=({role_id} if first else set()): raise RuntimeError('Unexpected role mentions')
            report['messages'].append({'message_id':sent['id'],'embeds':len(batch),
                'role_mention_confirmed':first,'url':'https://discord.com/channels/'+str(config['guild_id'])+'/'+str(config['channel_id'])+'/'+str(sent['id'])})
            save_report(receipt_path,report)
        report.update(status='confirmed',finished_utc=stamp(),role_ping_count=1)
        save_report(receipt_path,report)
        print(json.dumps(report,ensure_ascii=False,indent=2))
        return 0


if __name__=='__main__': raise SystemExit(asyncio.run(main()))
