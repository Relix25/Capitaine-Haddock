"""Discord HTTP transport and explicit loading of service credentials."""
import json
from pathlib import Path
import time
import urllib.error
import urllib.request


def load_discord_secrets(path='/etc/cours-watch/secrets.json'):
    path = Path(path)
    values = json.loads(path.read_text()) if path.exists() else {}
    env = path.with_name('.env')
    if env.exists():
        for line in env.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            key, value = line.split('=',1)
            if key.strip() == 'DISCORD_BOT_TOKEN':
                value = value.strip()
                if len(value) >= 2 and value[0] == value[-1] and value[0] in ('\"', "'"):
                    value = value[1:-1]
                values['DISCORD_BOT_TOKEN'] = value
    return values



def discord_request(url, payload, token=None, method='POST'):
    headers = {'Content-Type': 'application/json', 'User-Agent': 'UPBCourseWatch/1.0'}
    if token:
        headers['Authorization'] = 'Bot ' + token
    data = json.dumps(payload).encode()
    for attempt in range(3):
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                body = response.read()
                return json.loads(body) if body else {}
        except urllib.error.HTTPError as error:
            if error.code == 429 and attempt < 2:
                delay = min(60, max(1, float(json.loads(error.read()).get('retry_after', 5))))
                time.sleep(delay)
                continue
            # Never include the webhook URL/token in logs.
            raise RuntimeError(f'Discord HTTP {error.code}') from None
        except urllib.error.URLError:
            raise RuntimeError('Discord indisponible ; événement conservé pour reprise') from None
    raise RuntimeError('Limite Discord persistante')

