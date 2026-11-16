#!/usr/bin/env python3
"""Start the outbound Discord client, verify readiness, then switch delivery safely."""
import argparse
from datetime import datetime, timezone
import grp
import json
import os
from pathlib import Path
import subprocess
import time
from cours_watch import load_discord_secrets


def save(path, data):
    pending = path.with_name(path.name + '.pending')
    pending.write_text(json.dumps(data,ensure_ascii=False,indent=2) + '\n')
    pending.chmod(0o640)
    os.chown(pending,0,grp.getgrnam('courswatch').gr_gid)
    os.replace(pending,path)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--status',action='store_true')
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise SystemExit('root required')
    os.umask(0o077)
    folder = Path('/etc/cours-watch')
    config = json.loads((folder / 'config.json').read_text())
    secrets = load_discord_secrets(folder / 'secrets.json')
    cfg = config['discord']
    if args.status:
        print(json.dumps({'application_id':cfg.get('application_id'),
                          'bot_token_saved':bool(secrets.get('DISCORD_BOT_TOKEN')),
                          'mode':cfg['mode'],'presentation':cfg.get('presentation','text')}))
        return
    if not cfg.get('application_id') or not secrets.get('DISCORD_BOT_TOKEN'):
        raise SystemExit('Saisie privée du jeton à terminer avec configure_bot.py')
    marker = folder / 'bot-ready'
    marker.touch(mode=0o644)
    started = datetime.now(timezone.utc).replace(microsecond=0)
    subprocess.run(['systemctl','restart','cours-watch-bot.service'],check=True)
    status_path = Path('/var/lib/cours-watch/reports/bot-status.json')
    status = {}
    verified_fresh = False
    for _ in range(60):
        if status_path.exists():
            status = json.loads(status_path.read_text())
            checked = datetime.fromisoformat(status.get('checked_utc','1970-01-01T00:00:00+00:00'))
            main_pid = int(subprocess.check_output(['systemctl','show','-p','MainPID','--value','cours-watch-bot.service'],text=True).strip())
            if checked >= started and status.get('process_id') == main_pid and str(status.get('application_id')) == str(cfg['application_id']):
                if status.get('ready'):
                    verified_fresh = True
                    break
        time.sleep(1)
    if not verified_fresh:
        print(json.dumps({'activated':False,'webhook_preserved':True,'status':status},ensure_ascii=False))
        raise SystemExit(2)
    if not status.get('role_ping_allowed'):
        print(json.dumps({'activated':False,'commands_ready':True,'webhook_preserved':True,
                          'action':'Autoriser le bot à mentionner le rôle UPB dans le salon privé, ou rendre ce rôle mentionnable.'},ensure_ascii=False))
        raise SystemExit(2)
    cfg['mode'] = 'bot_channel'
    cfg['presentation'] = 'embed'
    cfg['interactive'] = True
    cfg.pop('pending_mode',None)
    save(folder / 'config.json',config)
    subprocess.run(['systemctl','enable','cours-watch-bot.service'],check=True)
    print(json.dumps({'activated':True,'commands':status['commands'],'private_buttons':True,
                      'public_port_opened':False,'mode':'bot_channel'},ensure_ascii=False))


if __name__ == '__main__':
    main()
