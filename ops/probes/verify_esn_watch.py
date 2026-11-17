"""Produce a non-secret live proof of the ESN monitor and existing bot health."""
import json
from pathlib import Path
import sqlite3
import subprocess
from datetime import datetime,timezone


def unit(name):
    raw=subprocess.check_output(['systemctl','show',name,'--property=ActiveState,SubState,Result,ExecMainStatus,UnitFileState,NextElapseUSecMonotonic'],text=True)
    return dict(line.split('=',1) for line in raw.splitlines() if '=' in line)


def main():
    data=Path('/var/lib/cours-watch')
    config=json.loads(Path('/etc/cours-watch/esn.json').read_text())
    report=json.loads((data/'reports/esn-status.json').read_text())
    bot=json.loads((data/'reports/bot-status.json').read_text())
    with sqlite3.connect('file:'+str(data/'esn.db')+'?mode=ro',uri=True) as db:
        known=db.execute('SELECT COUNT(*) FROM known_urls').fetchone()[0]
        pending=db.execute('SELECT COUNT(*) FROM outbox WHERE message_id IS NULL').fetchone()[0]
        confirmed=db.execute('SELECT COUNT(*) FROM outbox WHERE message_id IS NOT NULL').fetchone()[0]
    proof={'verified_utc':datetime.now(timezone.utc).isoformat(),'report':report,
        'role_id':config.get('role_id'),'channel_id':config['channel_id'],
        'known_urls':known,'pending':pending,'confirmed':confirmed,
        'bot_ready':bot.get('ready'),'bot_commands':bot.get('commands'),
        'units':{name:unit(name) for name in ('cours-watch-esn.service','cours-watch-esn.timer','cours-watch-bot.service')}}
    print(json.dumps(proof,ensure_ascii=False,indent=2))
    output=Path('/tmp/esn-watch-proof.json')
    output.write_text(json.dumps(proof,ensure_ascii=False,indent=2)+'\n'); output.chmod(0o644)


if __name__=='__main__': main()
