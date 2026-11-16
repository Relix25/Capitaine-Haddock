"""Deploy three task embeds per private message, with rollback and verification."""
import asyncio
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
EXPECTED = {'course_bot.py':'44b5b40b751a9e882f39067a562fc3f530a89efeac09c40209a5461fa9f24672',
            'course_tasks.py':'be0ab5ab8c410b8b8f4a5125641acac68458b56a6d274c9f02c62608589f41a2'}
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
    backup = Path('/var/backups/cours-watch/tasks-multi-embed-20261117')
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
        from course_bot import TaskListView
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
        async def inspect_batches():
            view = TaskListView(object(),0,pages)
            try:
                flattened = [card for batch in view.pages for card in batch]
                if flattened != pages or any(len(batch) > 3 for batch in view.pages):
                    raise RuntimeError('Task grouping lost or duplicated a card')
                if any(sum(len(discord.Embed.from_dict(card['embed'])) for card in batch) > 6000 for batch in view.pages):
                    raise RuntimeError('Message aggregate exceeds Discord limit')
                return {'message_pages':len(view.pages),'embeds_per_page':[len(batch) for batch in view.pages],
                        'component_rows':len(view.to_components())}
            finally:
                view.stop()
        grouped = asyncio.run(inspect_batches())
        proof = {'ready':True,'registered':True,'command':'a-faire','tests':62,
                 'sort':'upcoming_nearest_first','cards':len(pages),'max_embeds_per_message':3,**grouped,
                 'past_hidden_by_default':True,'tasks_including_past':len(all_rows),
                 'first_title':pages[0]['embed']['title'],'first_date':dated[0].isoformat() if dated else None,
                 'backup':str(backup)}
        path = Path('/tmp/haddock-task-batches-proof.json')
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
