"""Private task list from the same verified deadlines as daily reminders."""
from contextlib import closing
from datetime import datetime
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from course_reminders import resolve_task
from discord_presentation import safe, safe_url
from haddock_emojis import icon

PAGE_SIZE = 5


def task_rows(data_dir, config, course='tous', now=None):
    settings = json.loads(Path(config.get('reminders_file', '/etc/cours-watch/reminders.json')).read_text())
    tz = ZoneInfo(settings.get('timezone', 'Europe/Bucharest'))
    moment = now.astimezone(tz) if now else datetime.now(tz)
    rows = []
    with closing(sqlite3.connect(Path(data_dir, 'state.db').resolve().as_uri() + '?mode=ro', uri=True)) as db:
        state = SimpleNamespace(db=db)
        for task in settings.get('tasks', []):
            if course != 'tous' and task['course'] != course:
                continue
            resolved = resolve_task(state, task, moment, settings.get('policy', {}), include_past=True)
            if resolved:
                rows.append(resolved)
    # Dated tasks descend by deadline; dates needing validation stay last.
    rows.sort(key=lambda row: (row['task']['course'], row['task']['id']))
    rows.sort(key=lambda row: (row.get('due') is not None,
        row.get('due', datetime.min.replace(tzinfo=tz))), reverse=True)
    return rows, moment


def _plain(value, limit):
    # Source strings remain data and cannot create Discord mentions or formatting.
    return safe(' '.join(str(value).split())).replace('*', '').replace('`', '').replace('_', '')[:limit]


def task_pages(rows, now, course='tous'):
    if not rows:
        return [{'title': 'À faire · ' + _plain(course, 60), 'color': 0x3498DB,
                 'description': 'Aucune tâche suivie pour cette sélection.',
                 'footer': {'text': 'Liste issue des rappels configurés · /actualiser pour relire les plateformes'}}]
    pages = []
    count = (len(rows) + PAGE_SIZE - 1) // PAGE_SIZE
    for start in range(0, len(rows), PAGE_SIZE):
        fields = []
        for row in rows[start:start + PAGE_SIZE]:
            task = row['task']
            if row['unconfirmed']:
                when = 'DATE À CONFIRMER — ' + row['reason']
            else:
                due = row['due']
                when = due.strftime('%d/%m/%Y à %H:%M') if row['has_time'] else due.strftime('%d/%m/%Y') + ' · heure non publiée'
                if row['has_time'] and due.hour == due.minute == 0:
                    when += ' (début de journée)'
                if row['priority'] is None:
                    when += ' · date passée, rendu à vérifier'
                elif row['priority'][0] == 0:
                    when += ' · URGENT'
                elif row['priority'][0] == 1:
                    when += ' · IMPORTANT'
                if row['stale']:
                    when += '\nDernière date connue · source à revérifier'
            value = '**' + _plain(when, 340) + '**\n' + _plain(task.get('action', ''), 450)
            for key, label in (('url', 'Consignes'), ('planning_url', 'Planning')):
                url = safe_url(task.get(key))
                if url and not any(char in url for char in '<>'):
                    value += '\n[' + label + '](<' + url + '>)'
            fields.append({'name': _plain(task['course'] + ' · ' + task['title'], 180),
                           'value': value[:1024], 'inline': False})
        pages.append({'title': 'À faire · ' + _plain(course, 60),
            'description': (icon('project') + ' Échéances : plus récente → plus ancienne. Dates à confirmer en fin de liste.\n'
                           'Tâches suivies ; dépôt et passage à l’oral personnels non vérifiés.').lstrip(),
            'color': 0x3498DB, 'fields': fields,
            'footer': {'text': 'Page ' + str(len(pages) + 1) + '/' + str(count)
                + ' · ' + str(len(rows)) + ' tâches · Europe/Bucharest · /actualiser pour mettre à jour'}})
    return pages


def current_task_pages(data_dir, config, course='tous', now=None):
    rows, moment = task_rows(data_dir, config, course, now)
    return task_pages(rows, moment, course)
