#!/usr/bin/env python3
"""Interactive local setup; webhook secret is hidden and never printed."""
import getpass
import grp
import json
import os
from pathlib import Path
import re
import sys
import urllib.error
import urllib.request


def save_private(path, values):
    pending=path.with_name(path.name+'.pending')
    pending.write_text(json.dumps(values,ensure_ascii=False,indent=2)+'\n')
    os.chown(pending,0,grp.getgrnam('courswatch').gr_gid)
    pending.chmod(0o640)
    os.replace(pending,path)


def main():
    if os.geteuid()!=0:
        raise SystemExit('Lancer avec sudo pour configurer seulement /etc/cours-watch.')
    os.umask(0o077)
    print('Configuration du salon privé Discord. Aucun message ne sera envoyé.')
    webhook=getpass.getpass('Colle l’URL du webhook (saisie masquée) : ').strip()
    if not re.fullmatch(r'https://discord\.com/api(?:/v\d+)?/webhooks/\d{15,22}/[A-Za-z0-9_.-]+',webhook):
        raise SystemExit('URL Discord invalide ; rien enregistré.')
    request=urllib.request.Request(webhook,headers={'User-Agent':'UPBCourseWatch/1.0'})
    try:
        with urllib.request.urlopen(request,timeout=20) as response:
            metadata=json.load(response)
    except Exception:
        raise SystemExit('Webhook non validé ; vérifier l’URL et les permissions. Rien enregistré.') from None
    if not metadata.get('guild_id') or not metadata.get('channel_id'):
        raise SystemExit('Un webhook de salon de serveur est nécessaire. Rien enregistré.')
    users=[]
    for index in range(4):
        identity=input(f'Identifiant Discord de la personne {index+1}/4 : ').strip()
        if not re.fullmatch(r'\d{15,22}',identity) or identity in users:
            raise SystemExit('Identifiant invalide ou répété ; rien enregistré.')
        users.append(identity)
    folder=Path('/etc/cours-watch')
    config_path=folder/'config.json'
    config=json.loads(config_path.read_text())
    secret_path=folder/'secrets.json'
    secrets=json.loads(secret_path.read_text()) if secret_path.exists() else {}
    secrets['DISCORD_WEBHOOK_URL']=webhook
    config['discord']={'mode':'webhook','user_ids':users,'role_ids':[],
                       'guild_id':str(metadata['guild_id']),'channel_id':str(metadata['channel_id'])}
    save_private(secret_path,secrets)
    save_private(config_path,config)
    print('Discord configuré. Webhook conservé uniquement dans le fichier privé du VPS.')
    print('Serveur : '+str(metadata['guild_id'])+' ; salon : '+str(metadata['channel_id']))
    print('Quatre destinataires enregistrés. Aucun message envoyé ; activation après validation des plateformes.')


if __name__=='__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('\nConfiguration annulée.',file=sys.stderr)
        raise SystemExit(130)
