"""Display factual reminder messages for review without sending anything."""
import json
from pathlib import Path
from course_reminders import reminder_events
from cours_watch import State, format_notification

configuration = json.loads(Path('/etc/cours-watch/reminders.json').read_text())
state = State('/var/lib/cours-watch/state.db')
day, events = reminder_events(state, configuration)
for _, course, part, event in events:
    event['kind'] = 'initial_brief'
    event['title'] = 'Point actuel sur les projets — ' + day
    text = format_notification(event, '<@&1570209160744673411>' if event['mention'] else '', 'haddock')
    print(json.dumps({'course': course, 'part': part, 'length': len(text), 'message': text}, ensure_ascii=False))
