"""Discover/configure the ESN notification role without exposing credentials."""
import argparse
import grp
import json
import os
from pathlib import Path
import re
import urllib.error
import urllib.request

from cours_watch import discord_request,load_discord_secrets,stamp
from esn_watch import PROFILE,save_report


def save_config(path,value):
    temporary=path.with_suffix('.pending')
    temporary.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
    temporary.chmod(0o640); os.chown(temporary,0,grp.getgrnam('courswatch').gr_gid)
    os.replace(temporary,path)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--role-id')
    parser.add_argument('--create-role',action='store_true')
    parser.add_argument('--announce',action='store_true')
    args=parser.parse_args()
    if os.geteuid()!=0: raise SystemExit('Run as root')
    path=Path('/etc/cours-watch/esn.json')
    config=json.loads(path.read_text())
    token=load_discord_secrets()['DISCORD_BOT_TOKEN']
    def get(route):
        req=urllib.request.Request('https://discord.com/api/v10'+route,
            headers={'Authorization':'Bot '+token,'User-Agent':'UPBCourseWatch/1.0'})
        try:
            with urllib.request.urlopen(req,timeout=20) as response: return json.load(response)
        except urllib.error.HTTPError as error: raise RuntimeError('Discord HTTP '+str(error.code)) from None
    guild=str(config['guild_id'])
    roles=get('/guilds/'+guild+'/roles')
    matches=[role for role in roles if role['name'].casefold()=='esn'
             and (not args.role_id or role['id']==args.role_id)]
    if len(matches)>1: raise SystemExit('Multiple ESN roles; provide --role-id')
    role=matches[0] if matches else None
    if role is None and args.create_role:
        me=get('/users/@me')
        member=get('/guilds/'+guild+'/members/'+me['id'])
        permissions=0
        for entry in roles:
            if entry['id'] in {*member['roles'],guild}: permissions|=int(entry['permissions'])
        if not permissions&((1<<3)|(1<<28)):
            print(json.dumps({'configured':False,'reason':'manage_roles_required'})); return 2
        role=discord_request('https://discord.com/api/v10/guilds/'+guild+'/roles',
            {'name':'ESN','permissions':'0','hoist':False,'mentionable':True},token)
    if role is None:
        print(json.dumps({'configured':False,'reason':'ESN_role_missing','monitoring_active':True})); return 2
    if not role.get('mentionable'):
        print(json.dumps({'configured':False,'reason':'ESN_role_not_mentionable','role_id':role['id']})); return 2
    config['role_id']=role['id']
    save_config(path,config)
    result={'configured':True,'role_id':role['id'],'role_name':role['name'],'mentionable':True,'channel_id':config['channel_id']}
    receipt_path=Path('/var/lib/cours-watch/reports/esn-activation.json')
    previous=json.loads(receipt_path.read_text()) if receipt_path.exists() else {}
    if args.announce and previous.get('role_id')!=role['id']:
        response=discord_request('https://discord.com/api/v10/channels/'+str(config['channel_id'])+'/messages',
            {'content':'<@&'+role['id']+'> Mille sabords ! La veille ESN est en service.',
             'allowed_mentions':{'parse':[],'users':[],'roles':[role['id']]},
             'embeds':[{'title':'ESN Bucarest · Alertes activées','color':0x00AEEF,
             'description':'Contrôle du Linktree toutes les **5 minutes**. Une nouvelle activité déclenche une carte avec son lien et un ping **ESN**. Les liens déjà présents servent de référence.',
             'url':PROFILE}]},token)
        if str(response.get('channel_id'))!=str(config['channel_id']) or not response.get('id'):
            raise SystemExit('Unexpected Discord confirmation')
        previous={'role_id':role['id'],'channel_id':config['channel_id'],'message_id':response['id'],
                  'role_mention_confirmed':role['id'] in response.get('mention_roles',[]),'sent_utc':stamp()}
        save_report(receipt_path,previous)
        import pwd
        os.chown(receipt_path,pwd.getpwnam('courswatch').pw_uid,-1)
    result['activation']=previous
    print(json.dumps(result,ensure_ascii=False))
    return 0


if __name__=='__main__': raise SystemExit(main())
