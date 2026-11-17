"""Translate existing collected announcements; publish only with explicit --publish."""
import argparse
from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sqlite3
from zoneinfo import ZoneInfo

from courswatch.translation import eligible, prepare_events, with_cached_translation, display_event

DATA = Path('/var/lib/cours-watch')


def gathered(config):
    labels = {course['id']:course['label'] for course in config['courses']}
    items = []
    with closing(sqlite3.connect('file:' + str(DATA / 'state.db') + '?mode=ro',uri=True)) as db:
        for source,key,payload in db.execute('SELECT source,key,payload FROM items'):
            row = json.loads(payload)
            event = {**row,'kind':'new','source':source,'course':labels[source.split(':')[1]],'item_key':key}
            if eligible(event):
                items.append(event)
        old = [json.loads(row[0]) for row in db.execute('SELECT payload FROM outbox')]
    return items,[event for event in old if eligible(event)]


def dated(event):
    match = re.fullmatch(r'message-body-(\d{13})',event.get('item_key',''))
    return (datetime.fromtimestamp(int(match[1]) / 1000,timezone.utc).astimezone(ZoneInfo('Europe/Bucharest')).strftime('%d/%m/%Y à %H:%M')
            if match else 'Date de publication non relevée')


def archive_event(course,events):
    events = sorted(events,key=lambda event:event.get('item_key',''),reverse=True)
    details = ['Archives des messages déjà recueillis. Les dates et « aujourd’hui/demain » se rapportent au message original.',
               'Traduction automatique en français ; texte original conservé après chaque annonce.']
    preview = []
    for event in events:
        view = display_event(event)
        date = dated(event)
        details += ['','**' + event['source'].split(':')[0].capitalize() + ' · ' + date + ' · ' + view['title'] + '**',
                    view['body'],'','**Texte original**',event['title'],event['body'],'[Source](<' + event['url'] + '>)']
        if len(preview) < 3:
            preview.append({'title':view['title'],'date':date,'body':view['body']})
    return {'kind':'translation_archive','source':'archives:' + course,'course':course,
            'title':'Annonces traduites en français','body':'\n'.join(details),'url':'',
            'announcement_count':len(events),'preview':preview,'mention':False}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--publish',action='store_true')
    args = parser.parse_args()
    config = json.loads(Path('/etc/cours-watch/config.json').read_text())
    items,old = gathered(config)
    output = prepare_events(items + old,DATA)
    current = output[:len(items)]
    records = [{'source':e['source'],'item_key':e.get('item_key'),'original_title':e['title'],
                'original_body':e['body'],'url':e['url'],'translation':e.get('translation',{})} for e in output]
    report = DATA / 'reports/translations-backfill.json'
    report.write_text(json.dumps(records,ensure_ascii=False,indent=2))
    report.chmod(0o600)
    failed = [e for e in output if e.get('translation',{}).get('status') not in ('translated','not_needed')]
    summary = {'current_announcements':len(items),'historical_notifications':len(old),
               'translated':sum(e.get('translation',{}).get('status') == 'translated' for e in current),
               'already_french':sum(e.get('translation',{}).get('status') == 'not_needed' for e in current),
               'failed':[{'source':e['source'],'title':e['title'],'reason':e.get('translation',{}).get('reason')} for e in failed]}
    if args.publish:
        if failed:
            raise SystemExit('Unresolved translations; nothing published')
        from courswatch.runtime import State, load_discord_secrets, notify
        state = State(DATA / 'state.db')
        try:
            for course in dict.fromkeys(e['course'] for e in current):
                event = archive_event(course,[e for e in current if e['course'] == course])
                state.enqueue('translation-archive:fr:v1:' + course,event)
            summary['delivery'] = notify(state,config['discord'],load_discord_secrets())
        finally:
            state.db.close()
    print(json.dumps(summary,ensure_ascii=False,indent=2))


if __name__ == '__main__':
    main()
