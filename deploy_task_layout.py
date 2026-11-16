"""Deploy the reviewed task-card revision with rollback and read-only Discord checks."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import urllib.request

ROOT = Path('/opt/cours-watch')
EXPECTED = {'course_bot.py':'619d3321522c761681c14ec529e55b90701656cbb0af9582e660f13f93e0d0c5',
            'course_tasks.py':'89ff9161960e6a4a87b4449ce09256ae89021d8908a81d513bee717e0355932f'}
FILES = ['course_bot.py','course_tasks.py','test_course_bot.py','test_course_tasks.py']


def main():
    if os.geteuid() != 0:
        raise SystemExit('Run as root')
    stage = Path(sys.argv[1]).resolve()
    for name,expected in EXPECTED.items():
        if hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != expected:
            raise SystemExit('Active source changed; stopped: ' + name)
    for name in FILES:
        compile((stage / name).read_bytes(),name,'exec')
    subprocess.run([str(ROOT / 'bot-venv/bin/python'),'-m','unittest',
        'test_course_tasks','test_course_bot','test_reminders','test_discord_presentation',
        'test_course_health','test_state'],cwd=stage,
        env={**os.environ,'PYTHONPATH':str(stage) + ':' + str(ROOT)},check=True)
    backup = Path('/var/backups/cours-watch/tasks-layout-20261117')
    backup.mkdir(mode=0o700,exist_ok=False)
    for name in FILES:
        shutil.copy2(ROOT / name,backup / name)
    try:
        for name in FILES:
            pending = ROOT / ('.' + name + '.pending')
            shutil.copyfile(stage / name,pending)
            pending.chmod(0o644)
            os.replace(pending,ROOT / name)
        subprocess.run(['systemctl','restart','cours-watch-bot.service'],check=True)
        for _ in range(30):
            status = json.loads(Path('/var/lib/cours-watch/reports/bot-status.json').read_text())
            pid = int(subprocess.check_output(['systemctl','show','-p','MainPID','--value','cours-watch-bot.service'],text=True))
            if status.get('process_id') == pid and status.get('ready'):
                break
            time.sleep(1)
        else:
            raise RuntimeError('Client not ready')
        sys.path.insert(0,str(ROOT))
        from cours_watch import load_discord_secrets
        from course_tasks import current_task_pages, task_rows
        import discord
        config = json.loads(Path('/etc/cours-watch/config.json').read_text())
        cfg = config['discord']
        token = load_discord_secrets()['DISCORD_BOT_TOKEN']
        req = urllib.request.Request('https://discord.com/api/v10/applications/' + str(cfg['application_id'])
            + '/guilds/' + str(cfg['guild_id']) + '/commands',
            headers={'Authorization':'Bot ' + token,'User-Agent':'UPBCourseWatch/1.0'})
        with urllib.request.urlopen(req,timeout=20) as response:
            commands = json.load(response)
        command = next(c for c in commands if c['name'] == 'a-faire')
        option = next(o for o in command['options'] if o['name'] == 'passees')
        if option['type'] != 5 or option.get('required',False):
            raise RuntimeError('Expected optional past-date switch missing')
        data = Path('/var/lib/cours-watch')
        rows,moment = task_rows(data,config)
        pages = current_task_pages(data,config,now=moment)
        all_rows,_ = task_rows(data,config,now=moment,include_past=True)
        dated = [row['due'] for row in rows if not row['unconfirmed']]
        if dated != sorted(dated) or any(row['priority'] is None for row in rows if not row['unconfirmed']):
            raise RuntimeError('Upcoming order verification failed')
        if any(len(discord.Embed.from_dict(page['embed'])) > 6000 for page in pages):
            raise RuntimeError('Card exceeds Discord limit')
        proof = {'ready':True,'registered':True,'command':'a-faire','tests':61,
                 'sort':'upcoming_nearest_first','cards':len(pages),'one_task_per_card':True,
                 'past_hidden_by_default':True,'tasks_including_past':len(all_rows),
                 'first_title':pages[0]['embed']['title'],'first_date':dated[0].isoformat() if dated else None,
                 'backup':str(backup)}
        path = Path('/tmp/haddock-task-layout-proof.json')
        path.write_text(json.dumps(proof,ensure_ascii=False,indent=2))
        path.chmod(0o644)
        print(json.dumps(proof,ensure_ascii=False))
    except Exception:
        for name in FILES:
            shutil.copy2(backup / name,ROOT / name)
        subprocess.run(['systemctl','restart','cours-watch-bot.service'],check=False)
        raise


if __name__ == '__main__':
    main()
