"""Daily project reminders from observed course deadlines; no model or external writes."""
from datetime import date, datetime, time
import hashlib
import json
from pathlib import Path
import re
from zoneinfo import ZoneInfo

MONTHS = {name: index for index, name in enumerate(
    ['january','february','march','april','may','june','july','august','september','october','november','december'], 1)}


def body_hash(body):
    return hashlib.sha256(re.sub(r'\s+', ' ', body).strip().encode()).hexdigest()


def parse_moodle_due(body, timezone):
    match = re.search(r'\bDue:\s*(?:[A-Za-z]+,\s*)?(\d{1,2})\s+([A-Za-z]+)\s+(\d{4}),\s*(\d{1,2}):(\d{2})\s*(AM|PM)', body, re.I)
    if not match or match[2].lower() not in MONTHS:
        return None
    day, year, hour, minute = int(match[1]), int(match[3]), int(match[4]), int(match[5])
    hour = hour % 12 + (12 if match[6].upper() == 'PM' else 0)
    try:
        return datetime(year, MONTHS[match[2].lower()], day, hour, minute, tzinfo=timezone)
    except ValueError:
        return None


def urgency(due, now, has_time, important_days=3, urgent_days=1):
    days = (due.date() - now.date()).days
    if (has_time and due < now) or days < 0:
        return None
    if days <= urgent_days:
        return 0, '🔴 **URGENT**'
    if days <= important_days:
        return 1, '🟠 **IMPORTANT**'
    return 2, '📌 À prévoir'


def resolve_task(state, task, now, settings, *, include_past=False):
    if task.get('completed'):
        return None
    timezone = now.tzinfo
    reference = task['reference']
    row = state.db.execute('SELECT payload FROM items WHERE source=? AND key=?',
                           (reference['source'], reference['key'])).fetchone()
    if not row:
        return {'task': task, 'unconfirmed': True, 'reason': 'source non collectée'}
    item = json.loads(row[0])
    status = state.db.execute('SELECT status FROM sources WHERE source=?', (reference['source'],)).fetchone()
    stale = not status or status[0] != 'ok'
    if reference.get('document_sha256') and not stale:
        exists = state.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='document_checks'").fetchone()
        observed = state.db.execute(
            'SELECT d.status,d.generation=s.generation FROM document_checks d '
            'JOIN source_observations s ON s.source=d.source WHERE d.source=? AND d.key=?',
            (reference['source'], reference['key'])).fetchone() if exists else None
        if not observed or observed[0] != 'ok' or not observed[1]:
            return {'task': task, 'unconfirmed': True, 'reason': 'document non revérifié lors du dernier contrôle'}
    if task['date_mode'] == 'moodle_due':
        due = parse_moodle_due(item['body'], timezone)
        has_time = True
        if due is None:
            return {'task': task, 'unconfirmed': True, 'reason': 'date Moodle à revalider'}
    else:
        if reference.get('body_hash') and reference['body_hash'] != body_hash(item['body']):
            return {'task': task, 'unconfirmed': True, 'reason': 'annonce modifiée : calendrier à revalider'}
        if reference.get('document_sha256') and item['body'] != 'SHA256 ' + reference['document_sha256']:
            return {'task': task, 'unconfirmed': True, 'reason': 'document modifié : calendrier à revalider'}
        due = datetime.combine(date.fromisoformat(task['date']), time.min, tzinfo=timezone)
        has_time = False
    priority = urgency(due, now, has_time, settings.get('important_days', 3), settings.get('urgent_days', 1))
    if priority is None and not include_past:
        return None
    return {'task': task, 'due': due, 'has_time': has_time, 'priority': priority,
            'unconfirmed': False, 'stale': stale}


def reminder_events(state, configuration, now=None):
    timezone = ZoneInfo(configuration.get('timezone', 'Europe/Bucharest'))
    now = now.astimezone(timezone) if now else datetime.now(timezone)
    settings = configuration.get('policy', {})
    grouped = {}
    for task in configuration.get('tasks', []):
        resolved = resolve_task(state, task, now, settings)
        if resolved:
            grouped.setdefault(task['course'], []).append(resolved)
    rendered = []
    for course, tasks in grouped.items():
        tasks.sort(key=lambda x: (x.get('due', datetime.max.replace(tzinfo=timezone)), x['task']['id']))
        blocks = []
        for row in tasks:
            lines = []
            task = row['task']
            if row['unconfirmed']:
                lines.append('⚠️ **DATE À CONFIRMER — ' + task['title'] + '** : ' + row['reason'] + '.')
            else:
                due = row['due']
                when = due.strftime('%d/%m/%Y à %H:%M') if row['has_time'] else due.strftime('%d/%m/%Y') + ' — heure non publiée'
                remaining = (due.date() - now.date()).days
                countdown = 'aujourd’hui' if remaining == 0 else 'J-' + str(remaining)
                lines.append(row['priority'][1] + ' — **' + task['title'] + '** : **' + when + '** (' + countdown + ').')
                lines.append(task['action'])
                if due.hour == 0 and due.minute == 0 and row['has_time']:
                    lines.append('⚠️ Minuit au début de cette date : préparer le dépôt avant la fin de la veille.')
                if row['stale']:
                    lines.append('Dernière date connue ; plateforme inaccessible lors du dernier contrôle.')
            lines.append('[Source](<' + task['url'] + '>)')
            if task.get('planning_url'):
                lines.append('[Choisir son créneau](<' + task['planning_url'] + '>)')
            blocks.append('\n'.join(lines))
        # Split complete tasks only if a course grows beyond one Discord message.
        chunks = []
        current = []
        for block in blocks:
            if len('\n'.join(current + [block])) > 1200 and current:
                chunks.append('\n'.join(current))
                current = []
            current.append(block)
        if current:
            chunks.append('\n'.join(current))
        earliest = min((x['due'] for x in tasks if not x['unconfirmed']), default=datetime.max.replace(tzinfo=timezone))
        for part, body in enumerate(chunks):
            rendered.append((earliest, course, part, {'kind': 'daily_reminder', 'source': 'rappels:' + course,
                'course': course, 'title': 'Rappels projets — ' + now.strftime('%d/%m/%Y'),
                'body': body, 'url': '', 'mention': False}))
    rendered.sort(key=lambda x: (x[0], x[1], x[2]))
    if rendered:
        rendered[0][3]['mention'] = True
    return now.date().isoformat(), rendered


def enqueue_daily_reminders(state, config, now=None, initial=False):
    path = Path(config.get('reminders_file', '/etc/cours-watch/reminders.json'))
    if not path.exists():
        return {'enabled': False, 'queued': 0}
    configuration = json.loads(path.read_text())
    if not configuration.get('enabled', True):
        return {'enabled': False, 'queued': 0}
    timezone = ZoneInfo(configuration.get('timezone', 'Europe/Bucharest'))
    moment = now.astimezone(timezone) if now else datetime.now(timezone)
    if not initial and moment.hour < configuration.get('policy', {}).get('send_hour', 8):
        return {'enabled': True, 'queued': 0, 'status': 'scheduled_for_08_bucharest'}
    day, events = reminder_events(state, configuration, now)
    queued = 0
    for _, course, part, event in events:
        if initial:
            event['kind'] = 'initial_brief'
            event['title'] = 'Point actuel sur les projets — ' + moment.strftime('%d/%m/%Y')
        prefix = 'initial-brief:' if initial else 'daily-reminder:'
        queued += int(state.enqueue(prefix + day + ':' + course + ':' + str(part), event))
    return {'enabled': True, 'date': day, 'queued': queued, 'messages': len(events),
            'important_days': configuration.get('policy', {}).get('important_days', 3),
            'urgent_days': configuration.get('policy', {}).get('urgent_days', 1)}
