"""Guarded installation of /a-faire after tests, with metadata-only verification."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

ROOT = Path('/opt/cours-watch')
EXPECTED = {'course_bot.py':'4314a2bb2c2237a5ec151f4c9012860eeeb17023111c55aa0cc6a7cce90d5101',
            'course_reminders.py':'13f15e40417beed6913a46679dedab318a218371b7af1b3fb64f092e01cab2cb'}
FILES = ['course_bot.py','course_reminders.py','course_tasks.py','test_course_tasks.py',
         'test_course_bot.py','verify_bot_live.py']


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    if os.geteuid() != 0:
        raise SystemExit('Run as root')
    stage = Path(sys.argv[1]).resolve()
    for name, expected in EXPECTED.items():
        if digest(ROOT / name) != expected:
            raise SystemExit('Active file changed; installation stopped: ' + name)
    for name in FILES:
        if not (stage / name).is_file() or (stage / name).is_symlink():
            raise SystemExit('Missing staged file: ' + name)
    subprocess.run([str(ROOT / 'bot-venv/bin/python'), '-m','unittest',
        'test_course_tasks','test_course_bot','test_reminders','test_discord_presentation',
        'test_course_health','test_state'],cwd=stage,
        env={**os.environ,'PYTHONPATH':str(stage) + ':' + str(ROOT)},check=True)
    backup = Path('/var/backups/cours-watch/tasks-20261117')
    backup.mkdir(mode=0o700,exist_ok=False)
    previous = []
    for name in FILES:
        if (ROOT / name).exists():
            shutil.copy2(ROOT / name,backup / name)
            previous.append(name)
    (backup / 'manifest.json').write_text(json.dumps({'existing':previous,'files':FILES}))
    installed = False
    try:
        for name in FILES:
            pending = ROOT / ('.' + name + '.pending')
            shutil.copyfile(stage / name,pending)
            pending.chmod(0o644)
            os.replace(pending,ROOT / name)
        installed = True
        subprocess.run(['systemctl','restart','cours-watch-bot.service'],check=True)
        for _ in range(30):
            path = Path('/var/lib/cours-watch/reports/bot-status.json')
            status = json.loads(path.read_text())
            pid = int(subprocess.check_output(['systemctl','show','-p','MainPID','--value','cours-watch-bot.service'],text=True))
            if status.get('process_id') == pid and status.get('ready') and 'a-faire' in status.get('commands',[]):
                break
            time.sleep(1)
        else:
            raise RuntimeError('Client not ready after restart')
        subprocess.run([str(ROOT / 'bot-venv/bin/python'),str(ROOT / 'verify_bot_live.py')],cwd=ROOT,check=True)
        sys.path.insert(0,str(ROOT))
        from course_tasks import current_task_pages
        import discord
        config = json.loads(Path('/etc/cours-watch/config.json').read_text())
        pages = current_task_pages(Path('/var/lib/cours-watch'),config)
        if any(len(discord.Embed.from_dict(page)) > 6000 for page in pages):
            raise RuntimeError('Embed too long')
        proof = {'registered':True,'bot_ready':True,'command':'a-faire','pages':len(pages),
                 'tasks':sum(len(page.get('fields',[])) for page in pages),
                 'private':True,'sort':'deadline_desc','backup':str(backup)}
        output = Path('/tmp/cours-watch-task-command-proof.json')
        output.write_text(json.dumps(proof,ensure_ascii=False,indent=2))
        output.chmod(0o644)
        print(json.dumps(proof,ensure_ascii=False))
    except Exception:
        if installed:
            for name in FILES:
                if name in previous:
                    shutil.copy2(backup / name,ROOT / name)
                else:
                    (ROOT / name).unlink(missing_ok=True)
            subprocess.run(['systemctl','restart','cours-watch-bot.service'],check=False)
        raise


if __name__ == '__main__':
    main()
