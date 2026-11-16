#!/usr/bin/env python3
"""Read-only Moodle/Teams browser monitor. State and secrets stay outside this code tree."""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import hashlib
import json
import os
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path


def stamp():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


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


def clean(value):
    return re.sub(r'\s+', ' ', str(value or '')).strip()


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def canonical_forum_url(url):
    parsed = urllib.parse.urlsplit(url)
    discussion = urllib.parse.parse_qs(parsed.query).get('d', [''])[0]
    if parsed.hostname != 'curs.upb.ro' or not parsed.path.endswith('/mod/forum/discuss.php') or not discussion.isdigit():
        return None
    return urllib.parse.urlunsplit(('https', parsed.netloc, parsed.path, 'd=' + discussion, ''))


def moodle_cookies(data_dir):
    path = data_dir / 'moodle-cookies.json'
    if not path.exists():
        return []
    cookies = json.loads(path.read_text())
    if not isinstance(cookies, list) or any(c.get('domain', '').lstrip('.') != 'curs.upb.ro' for c in cookies):
        raise ValueError('État de session Moodle invalide')
    return cookies


def save_moodle_cookies(data_dir, cookies):
    # Only the dedicated VPS session is saved, never the local browser profile.
    selected = [c for c in cookies if c.get('domain', '').lstrip('.') == 'curs.upb.ro']
    if not any(c.get('name', '').lower().startswith('moodlesession') for c in selected):
        return
    path = data_dir / 'moodle-cookies.json'
    pending = path.with_suffix('.pending')
    pending.write_text(json.dumps(selected))
    pending.chmod(0o600)
    os.replace(pending, path)


@dataclass
class Item:
    key: str
    title: str
    body: str
    url: str

    @property
    def fingerprint(self):
        return digest([clean(self.title), clean(self.body), self.url])


@dataclass
class Result:
    source: str
    course: str
    status: str
    items: list[Item]
    detail: str = ''


class State:
    def __init__(self, path):
        self.db = sqlite3.connect(path)
        self.db.executescript('''
          PRAGMA journal_mode=WAL;
          CREATE TABLE IF NOT EXISTS sources(
            source TEXT PRIMARY KEY, status TEXT, initialized INTEGER NOT NULL DEFAULT 0, checked TEXT);
          CREATE TABLE IF NOT EXISTS items(
            source TEXT, key TEXT, fingerprint TEXT, payload TEXT,
            PRIMARY KEY(source,key));
          CREATE TABLE IF NOT EXISTS outbox(
            id INTEGER PRIMARY KEY AUTOINCREMENT, event_key TEXT UNIQUE,
            payload TEXT NOT NULL, delivered TEXT, created TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS deliveries(
            event_id INTEGER, destination TEXT, delivered TEXT NOT NULL,
            PRIMARY KEY(event_id,destination));
          CREATE TABLE IF NOT EXISTS discord_receipts(
            event_id INTEGER, destination TEXT, message_id TEXT, channel_id TEXT, role_ids TEXT,
            PRIMARY KEY(event_id,destination));
        ''')
        # A reply-anchor query parameter changes navigation, not the forum post.
        for source, key, payload in self.db.execute('SELECT source,key,payload FROM items WHERE key LIKE ?', ('forum:%',)).fetchall():
            previous = json.loads(payload)
            canonical = canonical_forum_url(previous['url'])
            if canonical:
                fragment = urllib.parse.urlsplit(previous['url']).fragment
                canonical += '#' + fragment if fragment else ''
                if previous['url'] != canonical:
                    previous['url'] = canonical
                    item = Item(**previous)
                    self.db.execute('UPDATE items SET fingerprint=?,payload=? WHERE source=? AND key=?',
                                    (item.fingerprint, json.dumps(previous, ensure_ascii=False), source, key))
        self.db.commit()

    def enqueue(self, event_key, event):
        cursor = self.db.execute('INSERT OR IGNORE INTO outbox(event_key,payload,created) VALUES(?,?,?)',
                                 (event_key, json.dumps(event, ensure_ascii=False), stamp()))
        self.db.commit()
        return bool(cursor.rowcount)

    def ingest(self, result: Result):
        row = self.db.execute('SELECT status,initialized FROM sources WHERE source=?', (result.source,)).fetchone()
        initialized = bool(row and row[1])
        previous_status = row[0] if row else None
        events = []
        if result.status != 'ok':
            # An unavailable source is never ingested as an empty successful snapshot.
            if previous_status != result.status:
                events.append({'kind': 'source_error', 'course': result.course,
                               'source': result.source, 'title': 'Vérification impossible',
                               'body': result.detail, 'url': ''})
        else:
            if previous_status and previous_status != 'ok':
                events.append({'kind': 'source_recovered', 'course': result.course,
                               'source': result.source, 'title': 'Accès rétabli', 'body': '', 'url': ''})
            for item in result.items:
                old = self.db.execute('SELECT fingerprint FROM items WHERE source=? AND key=?',
                                      (result.source, item.key)).fetchone()
                first_document_check = not old and item.key.endswith((':document', ':metadata'))
                if initialized and not first_document_check and (not old or old[0] != item.fingerprint):
                    events.append({'kind': 'modified' if old else 'new', 'course': result.course,
                                   'source': result.source, 'title': item.title,
                                   'body': item.body, 'url': item.url})
                self.db.execute('INSERT OR REPLACE INTO items VALUES(?,?,?,?)',
                                (result.source, item.key, item.fingerprint,
                                 json.dumps(asdict(item), ensure_ascii=False)))
            initialized = True
            # Missing items are not deletions: the browser UI may virtualize older posts.
        for event in events:
            event_key = digest([result.source, event, stamp(), time.time_ns()])
            self.db.execute('INSERT INTO outbox(event_key,payload,created) VALUES(?,?,?)',
                            (event_key, json.dumps(event, ensure_ascii=False), stamp()))
        self.db.execute('INSERT OR REPLACE INTO sources VALUES(?,?,?,?)',
                        (result.source, result.status, int(initialized), stamp()))
        self.db.commit()
        return events

    def pending(self):
        return [(row[0], json.loads(row[1])) for row in self.db.execute(
            'SELECT id,payload FROM outbox WHERE delivered IS NULL ORDER BY id')]

    def sent(self, event_id, destination):
        return self.db.execute('SELECT 1 FROM deliveries WHERE event_id=? AND destination=?',
                               (event_id, destination)).fetchone() is not None

    def mark_destination(self, event_id, destination, receipt=None):
        self.db.execute('INSERT OR REPLACE INTO deliveries VALUES(?,?,?)', (event_id, destination, stamp()))
        if receipt and receipt.get('id'):
            self.db.execute('INSERT OR REPLACE INTO discord_receipts VALUES(?,?,?,?,?)',
                            (event_id, destination, receipt['id'], receipt.get('channel_id', ''),
                             json.dumps(receipt.get('mention_roles', []))))
        self.db.commit()

    def mark_complete(self, event_id):
        self.db.execute('UPDATE outbox SET delivered=? WHERE id=?', (stamp(), event_id))
        self.db.commit()


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


def format_notification(event, mentions='', persona='plain'):
    body = '\n'.join(clean(line) for line in str(event['body']).splitlines() if clean(line)).replace('@', '@\u200b')
    heading = clean(event['title']).replace('@', '@\u200b')
    if persona == 'haddock':
        introduction = {
            'new': 'Mille sabords ! Du nouveau à l’horizon !',
            'modified': 'Tonnerre de Brest ! Une information suivie a changé !',
            'source_error': 'Mille sabords ! La vigie ne peut plus consulter cette plateforme.',
            'source_recovered': 'Le contact est rétabli, moussaillons !',
            'test': 'Mille sabords ! Le Capitaine Haddock prend son quart de veille.',
            'daily_reminder': 'Mille sabords ! Gardez ces projets en ligne de mire !',
            'initial_brief': 'Tonnerre de Brest ! Voici le point sur vos projets !'
        }.get(event['kind'], 'La vigie vous transmet des nouvelles !')
        title = f"{introduction}\n**{event['course']} · {event['source'].split(':')[0]}**"
    else:
        title = f"**{event['course']} · {event['source'].split(':')[0]} · {event['kind']}**"
    text = f'{mentions}\n{title}\n{heading}\n{body[:1250]}'
    url = event.get('url', '')
    if url:
        text += '\n<' + url + '>'
    return text[:1950]


def notify(state: State, configuration, secrets):
    mode = configuration.get('mode', 'disabled')
    if mode == 'disabled':
        return {'status': 'not_configured', 'pending': len(state.pending())}
    users = configuration.get('user_ids', [])
    roles = configuration.get('role_ids', [])
    if any(not re.fullmatch(r'\d{15,22}', value) for value in users + roles):
        raise ValueError('Identifiants Discord invalides')
    destinations = []
    if mode == 'webhook':
        webhook = secrets.get('DISCORD_WEBHOOK_URL', '')
        if not re.fullmatch(r'https://discord\.com/api(?:/v\d+)?/webhooks/\d+/[A-Za-z0-9_.-]+', webhook):
            raise ValueError('Webhook Discord absent ou invalide')
        destinations = [('webhook', webhook + '?wait=true', None)]
    elif mode == 'bot_channel':
        channel = str(configuration.get('channel_id', ''))
        token = secrets.get('DISCORD_BOT_TOKEN', '')
        if not re.fullmatch(r'\d{15,22}', channel) or not token:
            raise ValueError('Salon ou jeton du bot Discord absent')
        destinations = [('bot-channel:' + channel, 'https://discord.com/api/v10/channels/' + channel + '/messages', token)]
    elif mode == 'individual_dm':
        token = secrets.get('DISCORD_BOT_TOKEN', '')
        if not token or len(users) != 4:
            raise ValueError('Quatre destinataires et un jeton de bot sont nécessaires pour les DM individuels')
        for user in users:
            response = discord_request('https://discord.com/api/v10/users/@me/channels', {'recipient_id': user}, token)
            destinations.append((user, 'https://discord.com/api/v10/channels/' + response['id'] + '/messages', token))
    else:
        raise ValueError('Mode Discord inconnu')
    count = 0
    receipts = []
    pending_events = state.pending()
    from course_translation import prepare_events, display_event
    database_file = state.db.execute('PRAGMA database_list').fetchone()[2]
    localized = (prepare_events([event for _,event in pending_events],Path(database_file).parent)
                 if database_file else [event for _,event in pending_events])
    for (event_id, _), event in zip(pending_events,localized):
        # Fixed allow-list prevents source text from injecting @everyone or extra pings.
        ping = event.get('mention', True)
        mentions = ' '.join([f'<@{u}>' for u in users] + [f'<@&{r}>' for r in roles]) if mode in ('webhook','bot_channel') and ping else ''
        text = format_notification(display_event(event), mentions, configuration.get('persona', 'plain'))
        payload = {'content': text, 'allowed_mentions': {'parse': [], 'users': users if ping else [], 'roles': roles if ping else []}}
        if configuration.get('presentation') == 'embed':
            from discord_presentation import embed_payload
            payload = embed_payload(event, mentions, interactive=mode == 'bot_channel' and configuration.get('interactive', False))
            payload['allowed_mentions'] = {'parse': [], 'users': users if ping else [], 'roles': roles if ping else []}
        for destination, endpoint, token in destinations:
            if not state.sent(event_id, destination):
                receipt = discord_request(endpoint, payload, token)
                state.mark_destination(event_id, destination, receipt)
                if receipt.get('id'):
                    receipts.append({'event_id': event_id, 'message_id': receipt['id'],
                                     'channel_id': receipt.get('channel_id'), 'role_ids': receipt.get('mention_roles', [])})
        state.mark_complete(event_id)
        count += 1
    return {'status': 'ok', 'events_delivered': count, 'messages': receipts}


async def page_status(page, expected_host):
    parsed = urllib.parse.urlparse(page.url)
    if parsed.hostname != expected_host or '/login/' in parsed.path:
        return 'auth_required'
    if await page.locator('input[type="password"]').count():
        return 'auth_required'
    return None


async def moodle_pdf_items(context,course,row,pdf_store=None,urls=None):
    from moodle_pdf_versions import fetch_pdf,archive_pdf
    items=[]
    candidates=urls if urls is not None else [row['url']]
    for index,url in enumerate(candidates):
        sensor_key=(row['key'] if urls is None else row['key']+':attachment-'+hashlib.sha256(url.encode()).hexdigest()[:12])
        try:
            pdf=await fetch_pdf(context.request,url)
            items.append(Item(sensor_key+':document',row['title'],'SHA256 '+pdf['sha256'],row['url']))
            if pdf_store is not None:
                archive_key=row['key'] if index==0 else sensor_key
                try:
                    archive_pdf(pdf,pdf_store,course['id'],archive_key,clean(row['title']),row['url'])
                except (OSError,ValueError,RuntimeError):
                    items.append(Item(sensor_key+':document-status',row['title'],
                        'Empreinte PDF vérifiée, archivage des versions indisponible',row['url']))
        except Exception:
            items.append(Item(sensor_key+':document-status',row['title'],
                'Vérification du contenu du fichier indisponible',row['url']))
    return items


async def moodle(context, course, timeout,pdf_store=None):
    source = 'moodle:' + course['id']
    page = await context.new_page()
    try:
        response = await page.goto(course['moodle_url'], wait_until='domcontentloaded', timeout=timeout)
        if response and response.status >= 400:
            return Result(source, course['label'], 'network_error', [], f'Moodle HTTP {response.status}')
        status = await page_status(page, 'curs.upb.ro')
        if status:
            return Result(source, course['label'], status, [], 'Connexion Moodle à rétablir sur le VPS')
        # Read link/title and activity description. Do not hash completion buttons or personal grades.
        rows = await page.locator('li.activity').evaluate_all('''elements => elements.map(e => {
          const a=e.querySelector('a[href*="/mod/"]');
          if(!a) return null;
          const name=e.querySelector('.instancename,.activityname') || a;
          const description=e.querySelector('.description,.activity-description');
          return {key:e.id || a.href,title:name.textContent.trim(),
                  body:description ? description.innerText : '',url:a.href};
        }).filter(Boolean)''')
        if not rows:
            return Result(source, course['label'], 'selector_unverified', [], 'Page Moodle sans activités reconnues : contrôle humain nécessaire')
        items = [Item(**row) for row in rows]
        for row in rows:
            if urllib.parse.urlparse(row['url']).hostname != 'curs.upb.ro':
                continue
            if '/mod/resource/' in row['url']:
                items.extend(await moodle_pdf_items(context,course,row,pdf_store))
                continue
            if not any('/mod/'+kind+'/' in row['url'] for kind in ('assign','forum','folder')):
                continue
            detail = await context.new_page()
            try:
                await detail.goto(row['url'], wait_until='domcontentloaded', timeout=timeout)
                if await page_status(detail, 'curs.upb.ro'):
                    raise RuntimeError('auth_required')
                if '/mod/assign/' in row['url'] or '/mod/folder/' in row['url']:
                    if '/mod/assign/' in row['url']:
                        text = await detail.locator('#intro,.activity-dates,[data-region="activity-information"]').all_text_contents()
                        items.append(Item(row['key'] + ':dates', row['title'], clean(' '.join(text)), row['url']))
                    links=await detail.locator('a[href*="pluginfile.php"]').evaluate_all('els=>els.map(e=>e.href)')
                    urls=list(dict.fromkeys(url for url in links if urllib.parse.urlsplit(url).hostname=='curs.upb.ro'
                                            and urllib.parse.urlsplit(url).path.lower().endswith('.pdf')))
                    items.extend(await moodle_pdf_items(context,course,row,pdf_store,urls))
                else:
                    discussions = await detail.locator('a[href*="/mod/forum/discuss.php"]').evaluate_all(
                        'els=>els.map(e=>({url:e.href,title:e.innerText.trim()})).filter(e=>e.title)')
                    unique = {}
                    for discussion in discussions:
                        canonical = canonical_forum_url(discussion['url'])
                        if canonical:
                            unique.setdefault(canonical, {**discussion, 'url': canonical})
                    for discussion in list(unique.values())[:40]:
                        items.append(Item(discussion['url'], discussion['title'], '', discussion['url']))
                        await detail.goto(discussion['url'], wait_until='domcontentloaded', timeout=timeout)
                        if await page_status(detail, 'curs.upb.ro'):
                            raise RuntimeError('auth_required')
                        forum_posts = await detail.locator('article[data-content="forum-post"],article[id^="p"],.forumpost[id]').evaluate_all('''els=>els.map(e=>{
                          const body=e.querySelector('.post-content-container,.posting,.content');
                          return {key:e.id,title:(e.querySelector('h3,h4,h5,h6')||e).textContent.slice(0,180),
                                  body:(body||e).innerText};
                        })''')
                        if not forum_posts:
                            raise RuntimeError('forum_selector_unverified')
                        for post in forum_posts:
                            identity = post['key'] or digest([discussion['url'], post['body']])
                            items.append(Item('forum:'+identity, clean(post['title']), clean(post['body']),
                                              discussion['url']+('#'+post['key'] if post['key'] else '')))
            finally:
                await detail.close()
        return Result(source, course['label'], 'ok', items,
                      'Activités, dates, 40 discussions maximum par forum ; PDF accessibles via ressources, dossiers et pièces jointes, téléchargés et vérifiés par SHA256/pages jusqu’à 25 Mio ; indisponibilités signalées par fichier.')
    except Exception as error:
        kind = 'auth_required' if str(error) == 'auth_required' else 'network_error'
        return Result(source, course['label'], kind, [], 'Moodle inaccessible ou chargement incomplet')
    finally:
        await page.close()


async def click_optional(page, role, pattern):
    target = page.get_by_role(role, name=re.compile(pattern, re.I))
    if await target.count() == 1 and await target.is_visible():
        await target.click()
        return True
    return False


async def teams(context, course, timeout):
    source = 'teams:' + course['id']
    page = await context.new_page()
    try:
        await page.goto(course.get('teams_url') or 'https://teams.cloud.microsoft/', wait_until='domcontentloaded', timeout=timeout)
        try:
            await page.get_by_role('button', name=re.compile('Votre profil|Your profile', re.I)).wait_for(timeout=timeout)
        except Exception:
            return Result(source, course['label'], 'auth_required', [], 'Connexion Teams à rétablir sur le VPS')
        if not course.get('teams_url'):
            await click_optional(page, 'button', r'^Retour à toutes les équipes$|^Back to all teams$')
            tile = page.get_by_role('button', name=course['team_name'], exact=True)
            await tile.wait_for(timeout=timeout)
            await tile.click()
            await page.get_by_role('treeitem', name=re.compile(r'^Général$|^General$')).click()
        posts = page.locator('[id^="message-body-"]')
        await posts.first.wait_for(timeout=timeout)
        # Scroll within the actual message panel to reach the newest visible posts.
        for _ in range(4):
            await posts.last.evaluate('''el=>{
              let p=el.parentElement;
              while(p && p.scrollHeight<=p.clientHeight+5) p=p.parentElement;
              if(p) p.scrollTop=p.scrollHeight;
            }''')
            await page.wait_for_timeout(600)
        # Expand explicit reply-count and read-more controls, never the Reply composer.
        for pattern in [r'^voir plus$|^see more$', r'^\d+ réponses?$|^\d+ replies$']:
            controls = page.get_by_role('button', name=re.compile(pattern, re.I))
            for index in range(min(await controls.count(), 30)):
                candidate = controls.nth(index)
                with contextlib.suppress(Exception):
                    if await candidate.is_visible():
                        await candidate.click(timeout=2000)
        rows = await posts.evaluate_all('''elements=>elements.map(e=>{
          const heading=e.querySelector('h2,h3,[role="heading"]');
          const paragraphs=Array.from(new Set(Array.from(e.querySelectorAll('p,li')).map(p=>p.innerText.trim()).filter(Boolean)));
          const anchors=Array.from(e.querySelectorAll('a[href]')).map(a=>({text:a.innerText.trim(),url:a.href}));
          const clone=e.cloneNode(true);
          clone.querySelectorAll('button,time,[role="toolbar"]').forEach(x=>x.remove());
          return {key:e.id,title:heading ? heading.innerText : 'Publication',
             body:paragraphs.length ? paragraphs.join('\\n') : clone.innerText,
             links:anchors};
        })''')
        if not rows:
            return Result(source, course['label'], 'selector_unverified', [], 'Aucune publication Teams reconnue')
        items = []
        for row in rows:
            body = clean(row['body'])
            links = [x for x in row['links'] if urllib.parse.urlparse(x['url']).scheme == 'https']
            body += '\n' + '\n'.join(x['text'] + ' ' + x['url'] for x in links)
            items.append(Item(row['key'], row['title'], body, course.get('teams_url') or 'https://teams.cloud.microsoft/'))
        return Result(source, course['label'], 'ok', items,
                      'Publications/réponses effectivement chargées dans Général ; fichiers non annoncés et autres canaux hors couverture.')
    except Exception:
        return Result(source, course['label'], 'selector_unverified', [], 'Navigation Teams à revalider ; aucune absence de nouveauté déduite')
    finally:
        await page.close()


async def scan(config, data_dir, dry_run):
    from playwright.async_api import async_playwright
    results = []
    async with async_playwright() as playwright:
        context = await playwright.chromium.launch_persistent_context(
            str(data_dir/'browser-profile'), channel='chrome', headless=True, chromium_sandbox=True,
            locale='fr-FR', timezone_id='Europe/Bucharest', accept_downloads=False,
            viewport={'width': 1440, 'height': 1000})
        try:
            # Login tabs can be restored by a persistent Chrome profile. Release them
            # before scanning so Teams does not run twice within the memory budget.
            # Keep one blank tab: closing Chrome's last tab can end its cookie session.
            anchor = await context.new_page()
            for existing in list(context.pages):
                if existing != anchor:
                    await existing.close()
            for course in config['courses']:
                for collect in (moodle, teams):
                    if collect is moodle:
                        saved_cookies = moodle_cookies(data_dir)
                        if saved_cookies:
                            await context.clear_cookies(domain=re.compile(r'^\.?curs\.upb\.ro$'))
                            await context.add_cookies(saved_cookies)
                    if collect is moodle:
                        export=config.get('obsidian_export',{})
                        store=Path(export.get('documents_dir','/var/lib/cours-watch-publish/documents')) if export.get('enabled') and not dry_run else None
                        result=await collect(context,course,config.get('timeout_ms',40000),pdf_store=store)
                    else:
                        result = await collect(context, course, config.get('timeout_ms', 40000))
                    results.append(result)
                    if collect is moodle and result.status == 'ok':
                        save_moodle_cookies(data_dir, await context.cookies())
                    print(json.dumps({'progress': result.source, 'status': result.status,
                                      'items': len(result.items), 'checked_utc': stamp()}),
                          file=sys.stderr, flush=True)
        finally:
            await context.close()
    state = State(data_dir/'state.db')
    changes = []
    reminders = {'status': 'dry_run'}
    if not dry_run:
        for result in results:
            changes.extend(state.ingest(result))
        from course_reminders import enqueue_daily_reminders
        reminders = enqueue_daily_reminders(state, config)
    secrets_path = Path(os.environ.get('COURS_WATCH_SECRETS', '/etc/cours-watch/secrets.json'))
    secrets = load_discord_secrets(secrets_path)
    delivery = {'status': 'dry_run'} if dry_run else notify(state, config.get('discord', {}), secrets)
    report = {'checked_utc': stamp(), 'dry_run': dry_run,
              'sources': [{'source': x.source, 'course': x.course, 'status': x.status,
                           'items': len(x.items), 'detail': x.detail} for x in results],
              'changes': changes, 'reminders': reminders, 'delivery': delivery}
    reports = data_dir/'reports'
    reports.mkdir(mode=0o700, exist_ok=True)
    from course_capture import publish_snapshot
    try:
        report['obsidian_export'] = publish_snapshot(data_dir,config,report,results)
    except (OSError,ValueError,sqlite3.Error) as error:
        report['obsidian_export'] = {'status':'error','error_type':type(error).__name__}
    (reports/'latest.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if all(x.status == 'ok' for x in results) else 2


async def keep_moodle_session(config, data_dir):
    """Read one protected page through HTTP; no browser and no course-change notification."""
    from playwright.async_api import async_playwright
    cookies = moodle_cookies(data_dir)
    status = 'auth_required'
    if cookies:
        async with async_playwright() as p:
            request = await p.request.new_context(storage_state={'cookies': cookies, 'origins': []})
            try:
                response = await request.get(config['courses'][0]['moodle_url'], timeout=45000)
                url = urllib.parse.urlsplit(response.url)
                html = await response.text()
                if (response.ok and url.hostname == 'curs.upb.ro' and '/course/view.php' in url.path
                        and re.search(r'<li\b[^>]*class=[\"\x27][^\"\x27]*\bactivity\b', html, re.I)):
                    status = 'ok'
                    save_moodle_cookies(data_dir, (await request.storage_state())['cookies'])
            except Exception:
                status = 'network_error'
            finally:
                await request.dispose()
    report = {'checked_utc': stamp(), 'status': status, 'purpose': 'moodle_session_keepalive'}
    (data_dir / 'reports').mkdir(mode=0o700, exist_ok=True)
    (data_dir / 'reports' / 'moodle-session.json').write_text(json.dumps(report))
    print(json.dumps(report))
    return 0 if status == 'ok' else 2


def import_cookies(path, data_dir):
    """Import only a user-provided cookie export; never scrape a desktop browser profile."""
    values = json.loads(Path(path).read_text(encoding='utf-8-sig'))
    values = values.get('cookies', []) if isinstance(values, dict) else values
    if not isinstance(values, list) or not values:
        raise ValueError('Fichier de cookies vide ou invalide')
    allowed = ('curs.upb.ro', 'teams.cloud.microsoft', 'teams.microsoft.com',
               'login.microsoftonline.com', 'login.live.com', 'login.windows.net')
    cookies = []
    for item in values:
        domain = item.get('domain', '').lstrip('.').lower()
        if not any(domain == x or domain.endswith('.'+x) for x in allowed):
            raise ValueError('Cookie extérieur aux plateformes autorisées : import annulé')
        c = {key: item[key] for key in ('name', 'value', 'domain', 'path', 'httpOnly', 'secure') if key in item}
        c.setdefault('path', '/')
        expiry = item.get('expires', item.get('expirationDate', -1))
        c['expires'] = float(expiry) if expiry else -1
        same = item.get('sameSite', 'Lax')
        c['sameSite'] = {'no_restriction': 'None', 'unspecified': 'Lax', 'lax': 'Lax', 'strict': 'Strict'}.get(same, same)
        cookies.append(c)
    async def apply():
        from playwright.async_api import async_playwright
        async with async_playwright() as p:
            context = await p.chromium.launch_persistent_context(str(data_dir/'browser-profile'), channel='chrome', headless=True, chromium_sandbox=True)
            try:
                await context.add_cookies(cookies)
                save_moodle_cookies(data_dir, cookies)
            finally:
                await context.close()
    asyncio.run(apply())
    print(json.dumps({'cookies_imported': len(cookies), 'values_logged': False}))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['scan', 'login', 'import-cookies', 'status', 'keepalive', 'remind', 'brief'])
    parser.add_argument('--config', default='/etc/cours-watch/config.json')
    parser.add_argument('--data-dir', default='/var/lib/cours-watch')
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--cookie-file')
    args = parser.parse_args()
    os.umask(0o077)
    data_dir = Path(args.data_dir)
    data_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    # GUI login, daily scan and HTTP session refresh must not overwrite each other.
    profile_lock = None
    if args.command != 'status' and os.name == 'posix':
        import fcntl
        profile_lock = (data_dir / 'browser.lock').open('a')
        try:
            fcntl.flock(profile_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print(json.dumps({'status': 'busy', 'action': 'skipped_without_session_change'}))
            return 0
    if args.command == 'import-cookies':
        if not args.cookie_file:
            parser.error('--cookie-file requis')
        import_cookies(args.cookie_file, data_dir)
        return 0
    if args.command == 'status':
        report = data_dir/'reports/latest.json'
        print(report.read_text() if report.exists() else '{"status":"not_run"}')
        return 0
    if args.command == 'login':
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            context = p.chromium.launch_persistent_context(str(data_dir/'browser-profile'), channel='chrome', headless=False,
                      chromium_sandbox=True, args=['--restore-last-session'], locale='fr-FR', timezone_id='Europe/Bucharest', viewport={'width': 1440, 'height': 1000})
            saved_cookies = moodle_cookies(data_dir)
            if saved_cookies:
                context.add_cookies(saved_cookies)
            page = context.new_page()
            with contextlib.suppress(Exception):
                page.goto('https://teams.cloud.microsoft/')
            moodle_page = context.new_page()
            with contextlib.suppress(Exception):
                moodle_page.goto('https://curs.upb.ro/2026/my/', wait_until='domcontentloaded', timeout=45000)
            while context.pages:
                with contextlib.suppress(Exception):
                    for current in context.pages:
                        url = urllib.parse.urlsplit(current.url)
                        if (url.hostname == 'curs.upb.ro' and ('/my/' in url.path or '/course/' in url.path)
                                and not current.locator('input[type="password"]').count()):
                            save_moodle_cookies(data_dir, context.cookies())
                            break
                    context.pages[0].wait_for_timeout(2000)
        return 0
    configuration = json.loads(Path(args.config).read_text())
    if args.command in ('remind', 'brief'):
        from course_reminders import enqueue_daily_reminders
        state = State(data_dir / 'state.db')
        reminders = enqueue_daily_reminders(state, configuration, initial=args.command == 'brief')
        secret_path = Path(os.environ.get('COURS_WATCH_SECRETS', '/etc/cours-watch/secrets.json'))
        secrets = load_discord_secrets(secret_path)
        report = {'checked_utc': stamp(), 'reminders': reminders,
                  'delivery': notify(state, configuration.get('discord', {}), secrets)}
        (data_dir / 'reports').mkdir(mode=0o700, exist_ok=True)
        (data_dir / 'reports/latest-reminders.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    if args.command == 'keepalive':
        return asyncio.run(keep_moodle_session(configuration, data_dir))
    return asyncio.run(scan(configuration, data_dir, args.dry_run))


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (ValueError, RuntimeError) as error:
        print(json.dumps({'status': 'configuration_error', 'message': str(error)}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(3)
