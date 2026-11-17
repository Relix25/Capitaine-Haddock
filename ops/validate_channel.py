"""Explicit synthetic Discord channel check; never mention roles or users."""
from __future__ import annotations

import argparse
import asyncio
from copy import deepcopy
import json
from pathlib import Path
import re
from types import SimpleNamespace
import time
import urllib.error
import urllib.request

API = 'https://discord.com/api/v10'
NO_MENTIONS = {'parse': [], 'users': [], 'roles': [], 'replied_user': False}
COMMANDS = {'cours', 'actualiser', 'status', 'a-faire'}


def safe_payload(payload):
    """Enforce the test's no-ping rule independently of the view's defaults."""
    def clean(value):
        if isinstance(value, str):
            return value.replace('@', '@\u200b')
        if isinstance(value, list):
            return [clean(item) for item in value]
        if isinstance(value, dict):
            return {key: clean(item) for key, item in value.items()}
        return value

    result = clean(deepcopy(payload))
    result['allowed_mentions'] = deepcopy(NO_MENTIONS)
    # These callbacks are exercised by an adapter, not by a Discord user.
    # Disable navigation during the short check, then remove all components.
    for row in result.get('components', []):
        for button in row.get('components', []):
            if button.get('type') == 2 and button.get('style') != 5:
                button['disabled'] = True
    return result


def _transport(token):
    def request(path, payload=None, method='GET'):
        if not re.fullmatch(r'/(?:users/@me|applications/\d+/guilds/\d+/commands|channels/\d+(?:/messages(?:/\d+)?)?)', path):
            raise ValueError('Unsupported validation endpoint')
        if method not in {'GET', 'POST', 'PATCH'}:
            raise ValueError('Unsupported validation method')
        body = None if payload is None else json.dumps(safe_payload(payload)).encode()
        headers = {'Authorization': 'Bot ' + token, 'User-Agent': 'HaddockChannelValidation/1.0'}
        if body is not None:
            headers['Content-Type'] = 'application/json'
        for attempt in range(3):
            try:
                req = urllib.request.Request(API + path, data=body, headers=headers, method=method)
                with urllib.request.urlopen(req, timeout=20) as response:
                    return json.load(response)
            except urllib.error.HTTPError as error:
                if error.code == 429 and attempt < 2:
                    try:
                        delay = min(20, max(1, float(json.load(error).get('retry_after', 2))))
                    except (ValueError, TypeError):
                        delay = 2
                    time.sleep(delay)
                    continue
                raise RuntimeError('Discord validation HTTP ' + str(error.code)) from None
            except (urllib.error.URLError, OSError, ValueError):
                raise RuntimeError('Discord validation request failed') from None
        raise RuntimeError('Discord validation rate limit')
    return request


def _receipt(value, channel, message_id=None):
    if (not isinstance(value, dict) or not re.fullmatch(r'\d{15,22}', str(value.get('id', '')))
            or str(value.get('channel_id')) != channel
            or (message_id is not None and str(value['id']) != message_id)):
        raise RuntimeError('Unexpected validation receipt')
    if value.get('mention_roles') or value.get('mentions') or value.get('mention_everyone'):
        raise RuntimeError('Validation receipt contained a mention')
    return str(value['id'])


def synthetic_cards():
    return [{'embed': {'title': 'Validation technique · Carte ' + str(index),
                      'description': 'Contenu synthétique : aucune échéance réelle.',
                      'color': 0x95A5A6, 'footer': {'text': 'Test automatique sans mention'}},
             'links': [{'label': 'Ouvrir les consignes', 'url': 'https://curs.upb.ro/2026/my/'}]}
            for index in range(1, 5)]


def _view_payload(payload):
    return safe_payload({'content': 'Validation technique automatique · ' + payload['content'],
                         'embeds': [embed.to_dict() for embed in payload['embeds']],
                         'components': payload['view'].to_components()})


async def validate_channel(config, token, *, send_test=False, transport=None):
    """Exercise real view callbacks against one synthetic message, with no DB writes.

    This verifies the REST transport/rendering and callback-produced edits. It
    does not claim a user click, slash interaction, or ephemeral delivery.
    """
    if not send_test:
        raise ValueError('Explicit --send-test is required')
    cfg = config['discord']
    values = [str(cfg[name]) for name in ('guild_id', 'channel_id', 'application_id')]
    if any(not re.fullmatch(r'\d{15,22}', value) for value in values):
        raise ValueError('Invalid configured Discord scope')
    if not token:
        raise ValueError('Bot token not configured')
    guild, channel, application = values
    request = _transport(token) if transport is None else transport
    identity = await asyncio.to_thread(request, '/users/@me')
    if not identity.get('bot') or str(identity.get('id')) != application:
        raise RuntimeError('Unexpected bot identity')
    channel_info = await asyncio.to_thread(request, '/channels/' + channel)
    if str(channel_info.get('id')) != channel or str(channel_info.get('guild_id')) != guild:
        raise RuntimeError('Configured channel does not match the configured guild')
    commands = await asyncio.to_thread(request, '/applications/' + application + '/guilds/' + guild + '/commands')
    names = {command['name'] for command in commands}
    if not COMMANDS <= names:
        raise RuntimeError('Expected slash commands are missing')

    from courswatch.discord_app.bot import TaskListView
    view = TaskListView(SimpleNamespace(), 0, synthetic_cards())
    endpoint = '/channels/' + channel + '/messages'
    message_id = None
    edits = []

    async def edit(**payload):
        value = await asyncio.to_thread(request, endpoint + '/' + message_id,
                                        _view_payload(payload), 'PATCH')
        _receipt(value, channel, message_id)
        edits.append(len(payload['embeds']))

    try:
        payload = _view_payload(view.message_payload())
        payload.update(nonce=str(time.time_ns()), enforce_nonce=True)
        value = await asyncio.to_thread(request, endpoint, safe_payload(payload), 'POST')
        if (isinstance(value, dict) and str(value.get('channel_id')) == channel
                and re.fullmatch(r'\d{15,22}', str(value.get('id', '')))):
            message_id = str(value['id'])
        message_id = _receipt(value, channel)
        interaction = SimpleNamespace(response=SimpleNamespace(edit_message=edit))
        await view.next.callback(interaction)
        await view.previous.callback(interaction)
        final = safe_payload({'content': 'Validation technique du bot : OK. '
                              'Quatre commandes présentes, cartes et pagination vérifiées. '
                              'Aucun rôle ni utilisateur mentionné.',
                              'embeds': [], 'components': []})
        value = await asyncio.to_thread(request, endpoint + '/' + message_id, final, 'PATCH')
        _receipt(value, channel, message_id)
    except Exception:
        if message_id is not None:
            try:
                final = safe_payload({'content': 'Validation technique interrompue ; aucun ping demandé.',
                                      'embeds': [], 'components': []})
                await asyncio.to_thread(request, endpoint + '/' + message_id, final, 'PATCH')
            except Exception:
                pass
        raise
    finally:
        view.stop()
    return {'bot_id': str(identity['id']), 'guild_id': guild, 'channel_id': channel,
            'commands': sorted(names), 'message_id': message_id,
            'message_url': 'https://discord.com/channels/' + guild + '/' + channel + '/' + message_id,
            'message_pages': len(view.pages), 'initial_embeds': 3, 'edited_embed_counts': edits,
            'mention_roles': [], 'user_mentions': 0,
            'interaction_mode': 'callback_adapter_not_user_click'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--send-test', action='store_true')
    parser.add_argument('--config', type=Path, default=Path('/etc/cours-watch/config.json'))
    parser.add_argument('--secrets', type=Path, default=Path('/etc/cours-watch/secrets.json'))
    args = parser.parse_args(argv)
    if not args.send_test:
        parser.error('--send-test is required before accessing Discord or credentials')
    if __package__ in (None, ''):
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from courswatch.discord_app.transport import load_discord_secrets
    try:
        config = json.loads(args.config.read_text())
        token = load_discord_secrets(args.secrets).get('DISCORD_BOT_TOKEN', '')
        result = asyncio.run(validate_channel(config, token, send_test=True))
    except Exception as error:
        print(json.dumps({'status': 'error', 'error_type': type(error).__name__}))
        return 1
    print(json.dumps({'status': 'ok', **result}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
