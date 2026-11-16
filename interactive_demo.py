"""Post one real current card with an interactive details button; never ping for the demo."""
import json
from pathlib import Path
from bot_ui_data import current_events
from cours_watch import State, notify, load_discord_secrets

data = Path('/var/lib/cours-watch')
config = json.loads(Path('/etc/cours-watch/config.json').read_text())
if config['discord'].get('mode') != 'bot_channel' or not config['discord'].get('interactive'):
    raise SystemExit('Interactive bot not active')
events = current_events(data,config)
if not events:
    raise SystemExit('No current course cards to demonstrate')
state = State(data / 'state.db')
parts = {}
for event in events:
    course = event['course']
    part = parts.get(course,0)
    parts[course] = part + 1
    event['mention'] = False
    key = 'interactive-demo:' + config['discord']['application_id']
    if course != 'SOS' or part:
        key += ':' + course + ':' + str(part)
    state.enqueue(key,event)
secrets = load_discord_secrets()
print(json.dumps(notify(state,config['discord'],secrets),ensure_ascii=False,indent=2))
