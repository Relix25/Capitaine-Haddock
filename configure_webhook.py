#!/usr/bin/env python3
"""Receive the authorized webhook on stdin; never log its token or URL."""
import grp
import json
import os
from pathlib import Path
import re
import sys
import urllib.request


def save(path, value):
    pending=path.with_name(path.name+'.pending')
    pending.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
    os.chown(pending,0,grp.getgrnam('courswatch').gr_gid)
    pending.chmod(0o640)
    os.replace(pending,path)


def main():
    if os.geteuid()!=0:
        raise SystemExit('root required')
    os.umask(0o077)
    webhook=sys.stdin.read(8192).strip()
    if not re.fullmatch(r'https://discord\.com/api(?:/v\d+)?/webhooks/\d{15,22}/[A-Za-z0-9_.-]+',webhook):
        raise SystemExit('Invalid webhook; nothing saved')
    try:
        req=urllib.request.Request(webhook,headers={'User-Agent':'UPBCourseWatch/1.0'})
        with urllib.request.urlopen(req,timeout=20) as response:
            metadata=json.load(response)
    except Exception:
        raise SystemExit('Webhook validation failed; no credential logged or saved') from None
    if not metadata.get('guild_id') or not metadata.get('channel_id'):
        raise SystemExit('A server text-channel webhook is required')
    folder=Path('/etc/cours-watch')
    config_path=folder/'config.json'
    config=json.loads(config_path.read_text())
    secret_path=folder/'secrets.json'
    secrets=json.loads(secret_path.read_text()) if secret_path.exists() else {}
    secrets['DISCORD_WEBHOOK_URL']=webhook
    save(secret_path,secrets)
    config['discord']={'mode':'disabled','pending_mode':'webhook','persona':'haddock',
                       'user_ids':[],'role_ids':[],'expected_role_name':'UPB',
                       'guild_id':str(metadata['guild_id']),'channel_id':str(metadata['channel_id']),
                       'webhook_name':metadata.get('name','Capitaine Haddock')}
    save(config_path,config)
    print(json.dumps({'saved':True,'webhook_name':metadata.get('name'),
                      'guild_id':metadata['guild_id'],'channel_id':metadata['channel_id'],
                      'role_id_required':'UPB','delivery_enabled':False},ensure_ascii=False))


if __name__=='__main__':
    main()
