#!/usr/bin/env python3
"""Receive a bot token privately over the user's SSH terminal; never log it."""
import argparse
import getpass
import grp
import json
import os
from pathlib import Path
import re
import urllib.request
import urllib.error
from cours_watch import load_discord_secrets


def get(path, token):
    request = urllib.request.Request('https://discord.com/api/v10' + path,
        headers={'Authorization':'Bot ' + token,'User-Agent':'UPBCourseWatch/1.0'})
    try:
        with urllib.request.urlopen(request,timeout=25) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        raise SystemExit('Validation Discord HTTP ' + str(error.code) + '. Vérifie le jeton de la page Bot ; la clé publique et le client secret ne sont pas des jetons de bot. Aucun changement effectué.') from None
    except Exception:
        raise SystemExit('Validation Discord impossible. Aucun secret affiché ; aucun changement effectué.') from None


def save(path, data):
    pending = path.with_name(path.name + '.pending')
    pending.write_text(json.dumps(data,ensure_ascii=False,indent=2) + '\n')
    pending.chmod(0o640)
    os.chown(pending,0,grp.getgrnam('courswatch').gr_gid)
    os.replace(pending,path)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('application_id')
    parser.add_argument('--from-env',action='store_true')
    args = parser.parse_args()
    if os.geteuid() != 0 or not re.fullmatch(r'\d{15,22}',args.application_id):
        raise SystemExit('Root et Application ID valide requis')
    os.umask(0o077)
    token = (load_discord_secrets().get('DISCORD_BOT_TOKEN','') if args.from_env else
             getpass.getpass('Jeton du bot (saisie masquée ; jamais dans le chat) : ')).strip()
    if not re.fullmatch(r'[A-Za-z0-9_.-]{20,300}',token):
        raise SystemExit('Format du jeton invalide. Aucun changement.')
    application = get('/oauth2/applications/@me',token)
    user = get('/users/@me',token)
    if str(application.get('id')) != args.application_id or not user.get('bot'):
        raise SystemExit('Ce jeton ne correspond pas à l’application demandée. Aucun changement.')
    if application.get('interactions_endpoint_url'):
        raise SystemExit('Cette application utilise un endpoint Interactions HTTP. Choisis une application dédiée ou vide ce champ avant la connexion Gateway ; aucun changement effectué.')
    folder = Path('/etc/cours-watch')
    config_path = folder / 'config.json'
    config = json.loads(config_path.read_text())
    env = folder / '.env'
    pending_env = folder / '.env.pending'
    pending_env.write_text('DISCORD_BOT_TOKEN=' + token + '\n')
    pending_env.chmod(0o640)
    os.chown(pending_env,0,grp.getgrnam('courswatch').gr_gid)
    os.replace(pending_env,env)
    config['discord']['application_id'] = args.application_id
    config['discord']['presentation'] = 'embed'
    config['discord']['pending_mode'] = 'bot_channel'
    # The working webhook remains active until Gateway, commands and permissions are validated.
    save(config_path,config)
    print(json.dumps({'saved':True,'application_id':args.application_id,'bot_name':user.get('username'),
                      'current_delivery':config['discord']['mode'],'bot_activation_pending':True},ensure_ascii=False))


if __name__ == '__main__':
    main()
