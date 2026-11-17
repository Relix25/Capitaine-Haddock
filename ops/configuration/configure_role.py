#!/usr/bin/env python3
"""Configure the authorized Discord role and send one explicit test without logging secrets."""
import argparse
import grp
import json
import os
from pathlib import Path
import re

from courswatch.runtime import discord_request, format_notification, stamp


def save(path, data):
    pending = path.with_name(path.name + '.pending')
    pending.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')
    os.chown(pending, 0, grp.getgrnam('courswatch').gr_gid)
    pending.chmod(0o640)
    os.replace(pending, path)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('role_id')
    parser.add_argument('--test', action='store_true')
    args = parser.parse_args()
    if os.geteuid() != 0 or not re.fullmatch(r'\d{15,22}', args.role_id):
        raise SystemExit('Root and a valid role ID are required')
    os.umask(0o077)
    folder = Path('/etc/cours-watch')
    configuration = json.loads((folder / 'config.json').read_text())
    discord = configuration['discord']
    discord.update(mode='webhook', persona='haddock', user_ids=[], role_ids=[args.role_id])
    discord.pop('pending_mode', None)
    save(folder / 'config.json', configuration)
    receipt_path = folder / 'discord-test.json'
    if receipt_path.exists():
        previous = json.loads(receipt_path.read_text())
        if previous.get('role_id') == args.role_id and previous.get('channel_id') == discord['channel_id']:
            print(json.dumps({'configured': True, 'existing_test': previous}, ensure_ascii=False))
            return
    if not args.test:
        print(json.dumps({'configured': True, 'role_id': args.role_id, 'test_sent': False}))
        return
    secret = json.loads((folder / 'secrets.json').read_text())['DISCORD_WEBHOOK_URL']
    if not re.fullmatch(r'https://discord\.com/api(?:/v\d+)?/webhooks/\d+/[A-Za-z0-9_.-]+', secret):
        raise SystemExit('Invalid private webhook configuration')
    event = {'kind': 'test', 'course': 'UPB', 'source': 'veille:UPB',
             'title': 'Essai de transmission',
             'body': 'Tonnerre de Brest ! Le message de test est à bon port. '
                     'La veille quotidienne est prévue à 08 h, heure de Bucarest ; '
                     'elle démarrera après connexion et validation de Moodle et Teams.'}
    response = discord_request(secret + '?wait=true', {
        'content': format_notification(event, '<@&' + args.role_id + '>', 'haddock'),
        'allowed_mentions': {'parse': [], 'users': [], 'roles': [args.role_id]}
    })
    if not response.get('id') or str(response.get('channel_id')) != discord['channel_id']:
        raise SystemExit('Unexpected Discord receipt; inspect the channel before retrying')
    receipt = {'message_id': response['id'], 'channel_id': response['channel_id'],
               'guild_id': discord['guild_id'], 'role_id': args.role_id,
               'role_mention_confirmed': args.role_id in response.get('mention_roles', []),
               'sent_at': stamp()}
    save(receipt_path, receipt)
    print(json.dumps({'configured': True, 'test': receipt}, ensure_ascii=False))


if __name__ == '__main__':
    main()
