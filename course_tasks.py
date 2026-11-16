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

MONTHS_FR = ('janvier', 'février', 'mars', 'avril', 'mai', 'juin',
             'juillet', 'août', 'septembre', 'octobre', 'novembre', 'décembre')


def task_rows(data_dir, config, course='tous', now=None, *, include_past=False):
    settings = json.loads(Path(config.get('reminders_file', '/etc/cours-watch/reminders.json')).read_text())
    tz = ZoneInfo(settings.get('timezone', 'Europe/Bucharest'))
    moment = now.astimezone(tz) if now else datetime.now(tz)
    rows = []
    with closing(sqlite3.connect(Path(data_dir, 'state.db').resolve().as_uri() + '?mode=ro', uri=True)) as db:
        state = SimpleNamespace(db=db)
        for task in settings.get('tasks', []):
            if course != 'tous' and task['course'] != course:
                continue
            resolved = resolve_task(state, task, moment, settings.get('policy', {}), include_past=include_past)
            if resolved:
                rows.append(resolved)
    def order(row):
        if row['unconfirmed']:
            return (2, 0, row['task']['id'])
        if row['priority'] is None:
            return (1, -row['due'].timestamp(), row['task']['id'])
        return (0, row['due'].timestamp(), row['task']['id'])
    # Nearest upcoming deadline first; optional past dates, then unknown dates.
    rows.sort(key=order)
    return rows, moment


def _plain(value, limit):
    # Source strings remain data and cannot create Discord mentions or formatting.
    return safe(' '.join(str(value).split())).replace('*', '').replace('`', '').replace('_', '')[:limit]


def task_pages(rows, now, course='tous'):
    if not rows:
        return [{'embed': {'title': 'À faire · ' + _plain(course, 60), 'color': 0x3498DB,
                 'description': 'Aucune tâche à venir suivie pour cette sélection.\n\n'
                                'L’option **passees: vrai** permet de retrouver les dates antérieures.',
                 'footer': {'text': '/actualiser pour relire les plateformes'}}, 'links': []}]
    pages = []
    for index, row in enumerate(rows, 1):
        task = row['task']
        color = 0x3498DB
        if row['unconfirmed']:
            when = '**Date à confirmer**\n' + _plain(row['reason'], 300)
            color = 0xF39C12
        else:
            due = row['due']
            when = '**' + str(due.day) + ' ' + MONTHS_FR[due.month - 1] + ' ' + str(due.year) + '**'
            if row['has_time']:
                when += ' à **' + due.strftime('%H:%M') + '**'
                if due.hour == due.minute == 0:
                    when += ' — début de journée'
            else:
                when += '\nHeure non publiée'
            if row['priority'] is None:
                when += '\n\nDate passée · rendu personnel à vérifier'
                color = 0x95A5A6
            else:
                days = (due.date() - now.date()).days
                countdown = 'Aujourd’hui' if days == 0 else 'Demain' if days == 1 else 'Dans ' + str(days) + ' jours'
                if row['priority'][0] == 0:
                    countdown += ' · URGENT'
                    color = 0xE74C3C
                elif row['priority'][0] == 1:
                    countdown += ' · IMPORTANT'
                    color = 0xF39C12
                when += '\n\n**' + countdown + '**'
        fields = [{'name': 'À faire', 'value': _plain(task.get('action') or 'Consulter les consignes.', 1024),
                   'inline': False}]
        if row.get('stale'):
            fields.append({'name': 'Source à revérifier', 'value': 'Dernière date connue ; dernier accès à la plateforme en échec.',
                           'inline': False})
        links = []
        for key, label in (('url', 'Ouvrir les consignes'), ('planning_url', 'Choisir un créneau')):
            url = safe_url(task.get(key))
            if url and not any(char in url for char in '<>'):
                links.append({'label': label, 'url': url})
        embed = {'title': _plain(task['course'] + ' · ' + task['title'], 256),
                 'description': (icon('project', '📌') + ' ' + when).strip(),
                 'color': color, 'fields': fields,
                 'footer': {'text': 'Tâche ' + str(index) + '/' + str(len(rows))
                     + ' · Heure de Bucarest'}}
        if links:
            embed['url'] = links[0]['url']
        pages.append({'embed': embed, 'links': links})
    return pages


def current_task_pages(data_dir, config, course='tous', now=None, *, include_past=False):
    rows, moment = task_rows(data_dir, config, course, now, include_past=include_past)
    return task_pages(rows, moment, course)
