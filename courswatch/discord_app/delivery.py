"""Render and drain durable notifications independently from collection."""
from __future__ import annotations
from pathlib import Path
import re
from courswatch.models import clean
from courswatch.storage.state import State
from courswatch.discord_app.transport import discord_request


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



def notify(state: State, configuration, secrets, *, request=None):
    request = discord_request if request is None else request
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
            response = request('https://discord.com/api/v10/users/@me/channels', {'recipient_id': user}, token)
            destinations.append((user, 'https://discord.com/api/v10/channels/' + response['id'] + '/messages', token))
    else:
        raise ValueError('Mode Discord inconnu')
    count = 0
    receipts = []
    pending_events = state.pending()
    from courswatch.translation import prepare_events, display_event
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
            from courswatch.discord_app.presentation import embed_payload
            payload = embed_payload(event, mentions, interactive=mode == 'bot_channel' and configuration.get('interactive', False))
            payload['allowed_mentions'] = {'parse': [], 'users': users if ping else [], 'roles': roles if ping else []}
        for destination, endpoint, token in destinations:
            if not state.sent(event_id, destination):
                receipt = request(endpoint, payload, token)
                state.mark_destination(event_id, destination, receipt)
                if receipt.get('id'):
                    receipts.append({'event_id': event_id, 'message_id': receipt['id'],
                                     'channel_id': receipt.get('channel_id'), 'role_ids': receipt.get('mention_roles', [])})
        state.mark_complete(event_id)
        count += 1
    return {'status': 'ok', 'events_delivered': count, 'messages': receipts}

