#!/usr/bin/env python3
"""Discord commands and private detail buttons; outbound Gateway only."""
import asyncio
from datetime import datetime, timedelta, timezone
import json
import os
import random
import re
import signal
import sqlite3
import sys
import tempfile
import threading
import time
from dataclasses import replace
from pathlib import Path

import discord
from courswatch.settings import scan_command
from discord import app_commands

from courswatch.discord_app.ui_data import current_events, find_event, remember_private_card
from courswatch.discord_app.transport import discord_request, load_discord_secrets
from courswatch.models import stamp
from courswatch.discord_app.health import build_status_snapshot, status_embed, read_json
from courswatch.discord_app.tasks import current_task_pages
from courswatch.discord_app.emojis import button_emoji, icon
from courswatch.conversation.easter_egg import HaddockEasterEgg, triggered
from courswatch.conversation.voice import load_samples, choose_sample, send_voice_message, VoiceMessageError
from courswatch.conversation.chat import LunaChat
from courswatch.conversation.contracts import Scope, utc_iso
from courswatch.conversation.ingestion import envelope_from_message
from courswatch.discord_app.settings_ui import SettingsModal, authorize_settings
from courswatch.discord_app.presentation import compact_embed, interaction_allowed, private_details, DETAIL_CUSTOM_ID
from courswatch.discord_app.responses import prepare_private, send_private

DATA = Path(os.environ.get('COURS_WATCH_DATA','/var/lib/cours-watch'))
CONFIG = Path(os.environ.get('COURS_WATCH_CONFIG','/etc/cours-watch/config.json'))
SECRETS = Path(os.environ.get('COURS_WATCH_SECRETS','/etc/cours-watch/secrets.json'))
REPOSITORY = Path(__file__).resolve().parents[2]


def configuration():
    return json.loads(CONFIG.read_text())


class DetailsView(discord.ui.View):
    def __init__(self, client):
        super().__init__(timeout=None)
        self.client = client

    @discord.ui.button(label='Plus d’infos', style=discord.ButtonStyle.secondary,
                       emoji=discord.PartialEmoji.from_dict(button_emoji()), custom_id=DETAIL_CUSTOM_ID)
    async def details(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self.client.authorize(interaction):
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        event = await asyncio.to_thread(find_event, DATA, interaction.message.id, interaction.user.id)
        if event is None:
            await interaction.followup.send('Ce point n’est plus disponible. Utilise /cours pour afficher les données actuelles.', ephemeral=True)
            return
        pages = private_details(event)
        for page in pages[:30]:
            await interaction.followup.send(page, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
        if len(pages) > 30:
            await interaction.followup.send('Le détail est long : consulte également les liens des sources.', ephemeral=True)
        self.client.write_status(last_private_detail=datetime.now(timezone.utc).isoformat(timespec='seconds'))


class TaskListView(discord.ui.View):
    """Show up to three separate task embeds in one private message."""
    def __init__(self, client, user_id, pages):
        super().__init__(timeout=840)
        self.client = client
        self.user_id = user_id
        self.pages = []
        self.starts = []
        batch = []
        characters = 0
        for index, card in enumerate(pages):
            size = len(discord.Embed.from_dict(card['embed']))
            if size > 6000:
                raise ValueError('Task card exceeds Discord character limit')
            if batch and (len(batch) == 3 or characters + size > 6000):
                self.pages.append(batch)
                batch = []
                characters = 0
            if not batch:
                self.starts.append(index)
            batch.append(card)
            characters += size
        if batch:
            self.pages.append(batch)
        if not self.pages:
            raise ValueError('No task cards')
        self.page = 0
        self.update_buttons()

    def update_buttons(self):
        self.previous.disabled = self.page == 0
        self.next.disabled = self.page == len(self.pages) - 1
        self.clear_items()
        for offset, card in enumerate(self.pages[self.page], self.starts[self.page] + 1):
            for link in card.get('links', []):
                label = link['label'].replace('Ouvrir les consignes', 'Consignes').replace('Choisir un créneau', 'Créneau')
                row = 0 if link['label'] == 'Ouvrir les consignes' else 1
                self.add_item(discord.ui.Button(label=str(offset) + ' · ' + label, url=link['url'], row=row))
        self.add_item(self.previous)
        self.add_item(self.next)

    def message_payload(self):
        return {'content': '**À faire · Page ' + str(self.page + 1) + '/' + str(len(self.pages)) + '**',
                'embeds': [discord.Embed.from_dict(card['embed']) for card in self.pages[self.page]],
                'view': self, 'allowed_mentions': discord.AllowedMentions.none()}

    async def interaction_check(self, interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message('Utilise /a-faire pour ouvrir ta propre liste.', ephemeral=True)
            return False
        return await self.client.authorize(interaction)

    async def move(self, interaction, offset):
        self.page = max(0, min(len(self.pages) - 1, self.page + offset))
        self.update_buttons()
        await interaction.response.edit_message(**self.message_payload())

    @discord.ui.button(label='← Précédent', style=discord.ButtonStyle.secondary, row=2)
    async def previous(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.move(interaction, -1)

    @discord.ui.button(label='Suivant →', style=discord.ButtonStyle.secondary, row=2)
    async def next(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.move(interaction, 1)


class CourseBot(discord.Client):
    def __init__(self, token):
        cfg = configuration()['discord']
        egg = cfg.get('easter_egg',{})
        self.easter_egg_enabled = bool(egg.get('enabled',False))
        ai = cfg.get('luna',{})
        self.luna_enabled = bool(ai.get('enabled',False))
        intents = discord.Intents.none()
        intents.guilds = True
        intents.guild_messages = self.easter_egg_enabled or self.luna_enabled
        intents.message_content = self.easter_egg_enabled or self.luna_enabled
        super().__init__(intents=intents, allowed_mentions=discord.AllowedMentions.none(),max_messages=None)
        self.token_value = token
        self.tree = app_commands.CommandTree(self)
        self.scope = discord.Object(id=int(cfg['guild_id']))
        self.refresh_lock = asyncio.Lock()
        self.easter_egg_lock = asyncio.Lock()
        self.easter_egg = HaddockEasterEgg(egg.get('cooldown_seconds',30))
        self.voice_probability = max(0,min(1,float(egg.get('voice_probability',0))))
        self.voice_samples = load_samples() if self.voice_probability else []
        self.last_voice_sample = None
        self.luna = LunaChat(self,DATA,ai,configuration) if self.luna_enabled else None
        if self.luna:
            from courswatch.media.service import MediaService
            self.luna.media = MediaService(DATA, self.luna.journal,
                retention_days=self.luna.journal.retention_days,
                describer=self.luna.brain.describe_media, profile_resolver=self.luna.brain.vision_profile,
                authorize=self.media_occurrence_allowed, refresh_source=self.refresh_media_source,
                authorize_dispatch=self.media_transmission_allowed)
        self.history_task = None
        self.history_retry_needed = False
        self.retention_task = None
        self.status_lock = threading.RLock()
        self.child = None
        self.commands_registered = False
        self.register_commands()

    def write_status(self, **extra):
        # Writers from Gateway and background work share one read/modify/write lock.
        with self.status_lock:
            path = DATA / 'reports/bot-status.json'
            previous = read_json(path)
            previous.update(extra)
            previous['process_id'] = os.getpid()
            previous['checked_utc'] = datetime.now(timezone.utc).isoformat(timespec='seconds')
            path.parent.mkdir(mode=0o700,exist_ok=True)
            pending = None
            try:
                with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                                 prefix='.bot-status-', delete=False) as stream:
                    pending = Path(stream.name)
                    pending.chmod(0o600)
                    json.dump(previous, stream, ensure_ascii=False, indent=2)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(pending,path)
            finally:
                if pending is not None:
                    pending.unlink(missing_ok=True)

    def finish_manual_scan(self, status, **extra):
        previous = read_json(DATA / 'reports/bot-status.json').get('last_manual_scan',{})
        previous.update(status=status,finished_utc=stamp(),**extra)
        self.write_status(last_manual_scan=previous)

    def record_response_timing(self, command, timing):
        # Only technical durations: no interaction IDs, users, arguments or text.
        if command not in ('a-faire', 'cours', 'status', 'actualiser'):
            return
        fields = ('gateway_ms', 'prepare_ms', 'ack_ms', 'send_ms', 'total_ms',
                  'deferred', 'initial_response')
        metadata = {'command': command, **{key: timing[key] for key in fields if key in timing}}
        try:
            self.write_status(last_command_timing=metadata)
        except OSError:
            pass  # A diagnostic write must not turn a delivered reply into a failure.

    async def authorize(self, interaction):
        cfg = configuration()['discord']
        roles = [role.id for role in getattr(interaction.user,'roles',[])]
        if interaction_allowed(interaction.guild_id,interaction.channel_id,roles,cfg):
            return True
        await interaction.response.send_message('Commande et détails réservés au rôle UPB dans le salon des cours.', ephemeral=True)
        return False

    async def setup_hook(self):
        self.add_view(DetailsView(self))
        cfg = configuration()['discord']
        # Upsert only our commands, preserving other commands of an existing app.
        for command in self.tree.get_commands(guild=self.scope):
            await asyncio.to_thread(discord_request,
                'https://discord.com/api/v10/applications/' + str(cfg['application_id']) + '/guilds/' + str(self.scope.id) + '/commands',
                command.to_dict(self.tree), self.token_value)
        # Retire only the old chatbot command, preserving other app commands.
        url = ('https://discord.com/api/v10/applications/' + str(cfg['application_id']) +
               '/guilds/' + str(self.scope.id) + '/commands')
        commands = await asyncio.to_thread(discord_request,url,{},self.token_value,method='GET')
        for command in commands:
            if command.get('name') in ('modele','modèle') and command.get('type',1) == 1:
                await asyncio.to_thread(discord_request,url+'/'+str(command['id']),{},self.token_value,method='DELETE')
        self.commands_registered = True

    async def on_ready(self):
        cfg = configuration()['discord']
        guild = self.get_guild(int(cfg['guild_id']))
        channel = guild.get_channel(int(cfg['channel_id'])) if guild else None
        permissions = channel.permissions_for(guild.me) if channel and guild.me else None
        if self.luna and channel and (self.history_task is None or self.history_task.done()):
            self.history_task = asyncio.create_task(self.resume_conversation_backfill(channel))
        if self.luna and (self.retention_task is None or self.retention_task.done()):
            self.retention_task = asyncio.create_task(self.conversation_maintenance())
        usable = bool(permissions and permissions.view_channel and permissions.send_messages and permissions.embed_links)
        role = guild.get_role(int(cfg['role_ids'][0])) if guild else None
        ping = bool(role and permissions and (role.mentionable or permissions.mention_everyone))
        self.write_status(ready=bool(usable and self.commands_registered), application_id=str(self.application_id),
                          commands=[command.name for command in self.tree.get_commands(guild=self.scope)], private_buttons=True, channel_access=usable, role_ping_allowed=ping,
                          easter_egg_enabled=self.easter_egg_enabled,message_content_intent=self.intents.message_content,
                          voice_samples=len(self.voice_samples),voice_probability=self.voice_probability,
                          luna_enabled=self.luna_enabled,luna_model=self.luna.brain.model if self.luna else None,
                          luna_provider=self.luna.brain.provider if self.luna else None,
                          luna_debug_enabled=self.luna.debug_enabled if self.luna else None,
                          luna_persona=self.luna.brain.persona_version if self.luna else None,
                          voice_enabled=bool(self.voice_probability and self.voice_samples))
        previous = read_json(DATA / 'reports/bot-status.json').get('last_manual_scan',{})
        if previous.get('status') == 'running' and previous.get('process_id') != os.getpid():
            self.finish_manual_scan('interrupted')
        print(json.dumps({'bot_ready':usable,'commands_registered':self.commands_registered,'role_ping_allowed':ping}),flush=True)

    async def on_disconnect(self):
        self.write_status(ready=False)

    async def on_resumed(self):
        await self.on_ready()

    async def on_error(self,event_method,*args,**kwargs):
        # Do not log event arguments or exception text: they can contain private messages.
        error = sys.exc_info()[1]
        metadata = {'event':event_method,'error_type':type(error).__name__,'checked_utc':stamp()}
        self.write_status(last_callback_error=metadata)
        print(json.dumps({'callback_failed':metadata}),flush=True)

    def conversation_scope(self):
        cfg = configuration()['discord']
        return Scope(str(cfg['guild_id']), str(cfg['channel_id']))

    def conversation_command_help(self, question):
        """Answer command questions from today's registry rather than old chat."""
        names=re.findall(r'/([\w-]+)', question.casefold())
        generic=bool(re.search(r'\b(?:quelles?|liste|aide|utiliser)\b.*\bcommandes?\b',question,re.I))
        if names and set(names)=={'jurons'} and not re.search(r'\b(comment|utiliser|aide|commande|signifie)\b',question,re.I):
            return ''  # Preserve conversational tone preferences.
        if not names and not generic:
            return ''
        registry={command.name:command for command in self.tree.get_commands(guild=self.scope)}
        lines=[]
        for name in names or list(registry):
            if name in ('modele','modèle'):
                lines.append('/modele a été retirée. Utilise /settings pour régler le modèle et le mode debug (administrateurs).')
            elif name in registry:
                lines.append('/'+name+' : '+registry[name].description)
            elif name == 'jurons':
                lines.append('/jurons exprime une préférence dans un message adressé à Haddock ; ce n’est pas une commande Discord enregistrée.')
            else:
                lines.append('/'+name+' n’est pas une commande Discord actuellement enregistrée.')
        selected=[]
        for line in dict.fromkeys(lines):
            if len('\n'.join([*selected,line]))<=1700:
                selected.append(line)
        return '\n'.join(selected)

    def media_occurrence_allowed(self, envelope):
        if envelope.scope != self.conversation_scope():
            return False
        channel = self.get_channel(int(envelope.scope.channel_id))
        guild = self.get_guild(int(envelope.scope.guild_id))
        if channel is None or guild is None or guild.me is None:
            return False
        permissions = channel.permissions_for(guild.me)
        return bool(permissions.view_channel)

    async def media_transmission_allowed(self, envelope, media):
        return await self.refresh_media_source(envelope, media) is not None

    async def refresh_media_source(self, envelope, media):
        from courswatch.media import media_identity

        if not self.media_occurrence_allowed(envelope):
            return None
        channel = self.get_channel(int(envelope.scope.channel_id))
        current = await self._fetch_conversation_source(channel, envelope)
        if current is None:
            return None
        return next((item for item in current.media if media_identity(item) == media_identity(media)), None)

    def conversation_message_allowed(self, message):
        if (message.guild is None or getattr(message.author, 'bot', False)
                or getattr(message, 'webhook_id', None)):
            return False
        cfg = configuration()['discord']
        roles = [role.id for role in getattr(message.author, 'roles', ())]
        return interaction_allowed(message.guild.id, message.channel.id, roles, cfg)

    async def conversation_rest_member(self, message, cache=None):
        """Distinguish membership denial from a temporary Discord failure."""
        if (message.guild is None or getattr(message.author, 'bot', False)
                or getattr(message, 'webhook_id', None)):
            return None
        scope = Scope(str(message.guild.id), str(message.channel.id))
        if scope != self.conversation_scope():
            return None
        key = (scope.guild_id, str(message.author.id))
        if cache is not None and key in cache:
            member = cache[key]
        else:
            fetch = getattr(message.guild, 'fetch_member', None)
            try:
                member = await fetch(message.author.id) if callable(fetch) else message.author
            except discord.NotFound:
                member = None
            if cache is not None:
                cache[key] = member
        cfg = configuration()['discord']
        if (member is None or str(member.id) != key[1] or
                not interaction_allowed(message.guild.id, message.channel.id,
                    [role.id for role in getattr(member, 'roles', ())], cfg)):
            return None
        return member

    async def _conversation_source_author(self, message, active, members):
        if not active.is_bot:
            return await self.conversation_rest_member(message, members)
        # Only prior replies from this installation can be used as bot history.
        if (self.user and message.author.bot and message.author.id == self.user.id
                and not getattr(message, 'webhook_id', None)):
            return message.author
        return None

    async def _fetch_conversation_source(self, channel, active, members=None):
        from courswatch.media import media_identity

        started = datetime.now(timezone.utc)
        scope, identity = active.scope, active.message_id
        invalidation = self.luna.invalidation(scope, identity)
        fetch = getattr(channel, 'fetch_message', None)
        if not callable(fetch):
            self.luna.invalidate(scope, identity)
            return None
        try:
            message = await fetch(int(identity))
        except discord.NotFound:
            self.luna.deleted(scope, identity)
            return None
        except (discord.HTTPException, OSError, ValueError):
            self.luna.invalidate(scope, identity)
            return None
        try:
            author = await self._conversation_source_author(message, active, members)
        except (discord.HTTPException, OSError):
            self.luna.invalidate(scope, identity)
            return None
        if (author is None or str(message.id) != identity or str(author.id) != active.author_id
                or scope != self.conversation_scope()):
            self.luna.invalidate(scope, identity)
            return None
        live = self.luna.journal.active(scope, identity)
        if (live is None or live.revision != active.revision
                or self.luna.invalidation(scope, identity) != invalidation):
            return None
        current = replace(envelope_from_message(message, author=author, now=started), event_kind='rest')
        if current.scope != scope or current.is_bot != active.is_bot:
            return None
        if live.edited_at and (not current.edited_at or current.edited_at < live.edited_at):
            # An out-of-order REST reply cannot approve a newer local revision.
            return None
        if active.is_bot:
            # Discord includes debug footers and voice attachment metadata. The
            # journal deliberately stores only the conversational reply itself.
            changed = current.edited_at != live.edited_at
        else:
            changed = (current.text != live.text or current.edited_at != live.edited_at
                       or current.reply_to_id != live.reply_to_id
                       or tuple(map(media_identity, current.media)) != tuple(map(media_identity, live.media)))
        if changed:
            self.luna.edited(current)
            return None
        self.luna.revalidated(scope, identity, live.revision, invalidation=invalidation)
        return current

    async def _conversation_versions_allowed(self, scope, versions, channel, *, publishing=False):
        guild = self.get_guild(int(scope.guild_id))
        if channel is None or scope != self.conversation_scope():
            return False
        if guild is not None and guild.me is not None:
            permissions = channel.permissions_for(guild.me)
            if not permissions.view_channel or (publishing and not permissions.send_messages):
                return False
        members = {}
        for identity, expected in versions.items():
            active = self.luna.journal.active(scope, identity)
            if active is None or active.deleted or (expected is not None and str(active.revision) != str(expected)):
                return False
            if await self._fetch_conversation_source(channel, active, members) is None:
                return False
        return scope == self.conversation_scope()

    @staticmethod
    def _conversation_evidence_versions(bundle, scope, versions):
        for item in (*bundle.evidence, *bundle.dependencies) if bundle is not None else ():
            if item.scope != scope:
                return False
            if item.kind not in ('conversation', 'media_observation'):
                continue
            identity, revision = item.source_id, item.version
            if item.kind == 'media_observation':
                try:
                    parts = json.loads(identity)
                    if not isinstance(parts, list) or len(parts) != 2:
                        return False
                    identity = str(parts[0])
                    revision = revision.split(':', 1)[0]
                except (ValueError, TypeError):
                    return False
            if versions.get(identity) is not None and str(versions[identity]) != revision:
                return False
            versions[identity] = revision
        return True

    async def conversation_dependencies_allowed(self, snapshot, bundle, message, *, publishing=False):
        """Recheck all used Discord occurrences before provider and publication."""
        scope = Scope(str(message.guild.id), str(message.channel.id))
        if snapshot is not None and (scope != snapshot.scope or str(message.id) != snapshot.reply_target_id):
            return False
        # Check the target first and fetch each message and member once per phase.
        versions = {str(message.id): None}
        if snapshot is not None:
            versions.update(snapshot.dependencies)
        if not self._conversation_evidence_versions(bundle, scope, versions):
            return False
        return await self._conversation_versions_allowed(scope, versions, message.channel, publishing=publishing)

    async def conversation_publish_allowed(self, message):
        return await self.conversation_dependencies_allowed(None, None, message, publishing=True)

    async def conversation_evidence_allowed(self, bundle):
        scope = self.conversation_scope()
        versions = {}
        if not self._conversation_evidence_versions(bundle, scope, versions):
            return False
        channel = self.get_channel(int(scope.channel_id))
        return not versions or await self._conversation_versions_allowed(scope, versions, channel)

    async def resume_conversation_backfill(self, channel):
        """Continue a bounded number of pages, yielding between pages."""
        for _ in range(4):
            if not await self.backfill_conversations(channel):
                return
            await asyncio.sleep(0)

    async def backfill_conversations(self, channel):
        """Ingest one complete Discord page silently and report continuation."""
        settings = configuration()['discord'].get('luna', {}).get('memory', {})
        if not settings.get('backfill_enabled', True):
            self.history_retry_needed = False
            return False
        scope = self.conversation_scope()
        if str(channel.id) != scope.channel_id:
            return False
        started = datetime.now(timezone.utc)
        end = started.replace(microsecond=started.microsecond // 1000 * 1000)
        start = end - timedelta(days=max(7, min(90, int(settings.get('backfill_days', 7)))))
        limit = max(1, min(10000, int(settings.get('backfill_limit', 1000))))
        journal = self.luna.journal
        after_id = discord.utils.time_snowflake(start, high=False) - 1
        cursor = journal.coverage(scope).get('cursor')
        if cursor:
            try:
                saved = json.loads(cursor)
                cursor_time = datetime.fromisoformat(saved['created_at'])
                window_end = datetime.fromisoformat(saved['window_end'])
                cursor_id = int(saved['message_id'])
                if saved.get('reconciled_pages') and start <= cursor_time <= window_end <= end:
                    start, end = cursor_time, window_end
                    after_id = cursor_id
            except (ValueError, TypeError, KeyError):
                pass
        # Discord excludes the entire millisecond at a datetime upper bound.
        through_id = discord.utils.time_snowflake(end, high=False) - 1
        seen, members, last_message = set(), {}, None
        try:
            guild = self.get_guild(int(scope.guild_id))
            if guild and guild.me:
                permissions = channel.permissions_for(guild.me)
                if not (permissions.view_channel and permissions.read_message_history):
                    self.history_retry_needed = True
                    self.write_status(conversation_backfill={'status':'unavailable','reason':'history_permission'})
                    return False
            async for message in channel.history(limit=limit, after=discord.Object(id=after_id),
                                                 before=end, oldest_first=True):
                if self.conversation_scope() != scope:
                    return False
                last_message = message
                identity = str(message.id)
                seen.add(identity)
                remembered = journal.active(scope, identity)
                if message.author.bot and remembered is None:
                    continue
                try:
                    member = (await self._conversation_source_author(message, remembered, members)
                              if remembered is not None else await self.conversation_rest_member(message, members))
                except (discord.HTTPException, OSError):
                    if remembered is not None:
                        self.luna.invalidate(scope, identity)
                    raise
                if member is None:
                    if remembered is not None:
                        self.luna.invalidate(scope, identity)
                    continue
                if remembered is not None and remembered.is_bot:
                    # This page cannot rewrite an already recorded conversational
                    # reply with Discord's debug footer or voice metadata.
                    continue
                envelope = envelope_from_message(message, ingestion_mode='backfill', now=started, author=member)
                self.luna.observe(replace(envelope, event_kind='rest'), message=None, mentioned=False)
            if self.conversation_scope() != scope:
                return False
            more = len(seen) == limit and last_message is not None
            page_end = last_message.created_at if more else end
            if more:
                if int(last_message.id) <= after_id:
                    return False
                through_id = int(last_message.id)
            # Coverage is certified after tombstoning the missing messages,
            # including the first page of a long window. Snowflakes partition
            # messages that share a timestamp between consecutive pages.
            journal.reconcile_window(scope, utc_iso(start), utc_iso(page_end), seen,
                                     after_message_id=after_id, through_message_id=through_id,
                                     observed_before=utc_iso(started))
            journal.record_coverage(scope, utc_iso(start), utc_iso(end), complete=not more,
                                    reason='limit' if more else '')
            if more:
                journal.record_coverage(scope, utc_iso(start), utc_iso(page_end), complete=True)
                journal.checkpoint(scope, cursor=json.dumps({'message_id':str(through_id),
                    'created_at':utc_iso(page_end),'window_end':utc_iso(end), 'reconciled_pages':True}))
            else:
                journal.checkpoint(scope, cursor='')
            journal.purge()
            self.history_retry_needed = False
            self.write_status(conversation_backfill={'status':'partial' if more else 'complete',
                'messages_seen':len(seen),'start_utc':utc_iso(start),'end_utc':utc_iso(end),'silent':True})
            return more
        except (discord.HTTPException, OSError, ValueError, sqlite3.Error):
            self.history_retry_needed = True
            self.write_status(conversation_backfill={'status':'unavailable','reason':'history_fetch','silent':True})
            return False

    async def conversation_maintenance(self):
        """Apply local retention and continue any pending bounded history scan."""
        while not self.is_closed() and self.luna:
            try:
                await asyncio.to_thread(self.luna.journal.purge)
                if self.luna.media is not None:
                    await asyncio.to_thread(self.luna.media.cache.purge)
            except (OSError, sqlite3.Error):
                self.write_status(conversation_retention={'status':'failed','checked_utc':stamp()})
            await asyncio.sleep(3600)
            if self.history_task is None or self.history_task.done():
                scope = self.conversation_scope()
                channel = self.get_channel(int(scope.channel_id))
                if channel and (self.history_retry_needed or self.luna.journal.coverage(scope).get('cursor')):
                    self.history_task = asyncio.create_task(self.resume_conversation_backfill(channel))

    async def on_raw_message_edit(self, payload):
        if not self.luna or getattr(payload, 'guild_id', None) is None:
            return
        scope = Scope(str(payload.guild_id), str(payload.channel_id))
        if scope != self.conversation_scope():
            return
        identity = str(payload.message_id)
        old = self.luna.journal.active(scope, identity)
        if old is None or old.deleted:
            return
        self.luna.invalidate(scope, identity)
        channel = self.get_channel(int(scope.channel_id))
        if channel is not None:
            # Reload full state so a partial update preserves absent fields.
            await self._fetch_conversation_source(channel, old)

    async def on_raw_message_delete(self, payload):
        if self.luna and getattr(payload, 'guild_id', None) is not None:
            scope = Scope(str(payload.guild_id), str(payload.channel_id))
            if scope == self.conversation_scope():
                self.luna.deleted(scope, str(payload.message_id))

    async def on_raw_bulk_message_delete(self, payload):
        if self.luna and getattr(payload, 'guild_id', None) is not None:
            scope = Scope(str(payload.guild_id), str(payload.channel_id))
            if scope == self.conversation_scope():
                for identity in payload.message_ids:
                    self.luna.deleted(scope, str(identity))

    async def on_message(self,message):
        received_at = time.monotonic()
        received_utc = datetime.now(timezone.utc)
        if (not (self.easter_egg_enabled or self.luna_enabled) or message.guild is None or message.author.bot
                or getattr(message,'webhook_id',None)):
            return
        cfg = configuration()['discord']
        roles = [role.id for role in getattr(message.author,'roles',[])]
        if not interaction_allowed(message.guild.id,message.channel.id,roles,cfg):
            return
        mentioned = bool(self.user and any(user.id == self.user.id for user in message.mentions))
        if self.luna:
            self.luna.accept(message,mentioned,received_at=received_at,received_utc=received_utc)
            return
        if not triggered(message.content,mentioned):
            return
        async with self.easter_egg_lock:
            quote = self.easter_egg.choose()
            if quote is None:
                return
            if self.voice_samples and random.random() < self.voice_probability:
                sample = choose_sample(self.voice_samples,self.last_voice_sample)
                try:
                    receipt = await asyncio.to_thread(send_voice_message,str(message.channel.id),self.token_value,
                        sample,nonce='hv' + str(message.id),reference_id=str(message.id))
                except (VoiceMessageError,OSError,ValueError) as error:
                    self.write_status(last_voice_error={'error_type':type(error).__name__,'checked_utc':stamp()})
                    # If the audio cannot be sent, preserve the existing text response.
                else:
                    self.last_voice_sample = sample.id
                    self.easter_egg.last_time = self.easter_egg.clock()
                    self.write_status(last_easter_egg={'sent_utc':stamp(),'reply_id':str(receipt['id']),
                        'format':'voice','sample_id':sample.id},last_easter_egg_error=None,last_voice_error=None)
                    return
            try:
                reply = await message.reply((icon('warning') + ' ' + quote).strip(),
                    mention_author=False,allowed_mentions=discord.AllowedMentions.none())
            except discord.HTTPException:
                self.write_status(last_easter_egg_error=stamp())
                return
            # Keep delivery metadata only, never the triggering message's text or author.
            self.easter_egg.last_time = self.easter_egg.clock()
            self.write_status(last_easter_egg={'sent_utc':stamp(),'reply_id':str(reply.id),'format':'text'},last_easter_egg_error=None)

    async def send_current(self, interaction, course='tous'):
        events, timing = await prepare_private(interaction, current_events, DATA, configuration(), course)
        if not events:
            await send_private(interaction, 'Aucun projet à venir confirmé pour cette sélection.', timing=timing)
            self.record_response_timing('cours', timing)
            return
        for index, event in enumerate(events):
            message = await send_private(interaction, embed=discord.Embed.from_dict(compact_embed(event,interactive=True)),
                                         view=DetailsView(self), fetch_message=True,
                                         timing=timing if index == 0 else None)
            if index == 0:
                name = getattr(getattr(interaction, 'command', None), 'name', 'cours')
                self.record_response_timing(name, timing)
            await asyncio.to_thread(remember_private_card,DATA,message.id,interaction.user.id,event)

    def register_commands(self):
        @self.tree.command(name='settings',description='Régler le modèle et le mode debug du chatbot (administrateurs).',guild=self.scope)
        @app_commands.default_permissions(administrator=True)
        async def settings(interaction:discord.Interaction):
            if not await authorize_settings(self,interaction):
                return
            await interaction.response.send_modal(SettingsModal(self,interaction.user.id))

        @self.tree.command(name='a-faire',description='Prochaines échéances en privé : trois tâches par message, une carte chacune.',guild=self.scope)
        @app_commands.describe(cours='Choisir un cours ou tous les cours',passees='Inclure aussi les dates passées, après les tâches à venir')
        @app_commands.choices(cours=[app_commands.Choice(name=name,value=name) for name in ['tous','AAIT','AITDM','Big Data','SOS']])
        async def a_faire(interaction:discord.Interaction,cours:str='tous',passees:bool=False):
            if not await self.authorize(interaction):
                return
            try:
                pages, timing = await prepare_private(interaction, current_task_pages, DATA, configuration(),
                                                      cours, include_past=passees)
                view = TaskListView(self,interaction.user.id,pages)
            except (OSError,ValueError,sqlite3.Error):
                await send_private(interaction, 'La liste est indisponible. /status indique le dernier état connu.')
                return
            await send_private(interaction, **view.message_payload(), timing=timing)
            self.record_response_timing('a-faire', timing)

        @self.tree.command(name='status',description='Vérifier la santé, le prochain point et les annonces déjà envoyées.',guild=self.scope)
        async def status(interaction:discord.Interaction):
            if not await self.authorize(interaction):
                return
            snapshot, timing = await prepare_private(interaction, build_status_snapshot, DATA, configuration(),
                                                      self.is_ready(), self.latency, self.refresh_lock.locked())
            await send_private(interaction, embed=discord.Embed.from_dict(status_embed(snapshot)), timing=timing)
            self.record_response_timing('status', timing)
            self.write_status(last_status_view=stamp())

        @self.tree.command(name='cours',description='Voir les projets et échéances, uniquement pour toi.',guild=self.scope)
        @app_commands.describe(cours='Choisir un cours ou tous les cours')
        @app_commands.choices(cours=[app_commands.Choice(name=name,value=name) for name in ['tous','AAIT','AITDM','Big Data','SOS']])
        async def cours(interaction:discord.Interaction,cours:str='tous'):
            if not await self.authorize(interaction):
                return
            await self.send_current(interaction,cours)

        @self.tree.command(name='actualiser',description='Relire Moodle et Teams puis afficher les projets en privé.',guild=self.scope)
        async def actualiser(interaction:discord.Interaction):
            if not await self.authorize(interaction):
                return
            if self.refresh_lock.locked():
                await interaction.response.send_message('Une collecte est déjà en cours. /cours affiche le dernier point disponible.',ephemeral=True)
                return
            async with self.refresh_lock:
                await interaction.response.defer(ephemeral=True,thinking=True)
                self.write_status(last_manual_scan={'status':'running','started_utc':stamp(),'process_id':os.getpid()})
                try:
                    command = scan_command(DATA, CONFIG)
                    # Script overrides retain the caller's directory and argv.
                    location = {}
                    if command[1:3] == ['-m', 'courswatch']:
                        command = scan_command(DATA.absolute(), CONFIG.absolute())
                        if os.sep in command[0]:
                            command[0] = str(Path(command[0]).absolute())
                        location['cwd'] = REPOSITORY
                    self.child = await asyncio.create_subprocess_exec(*command, stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE, start_new_session=True, **location)
                except OSError:
                    self.finish_manual_scan('failed',reason='start_failed')
                    await interaction.followup.send('La collecte n’a pas pu démarrer. /status indique le dernier état connu.',ephemeral=True)
                    return
                try:
                    output, _ = await asyncio.wait_for(self.child.communicate(),timeout=840)
                except asyncio.TimeoutError:
                    await self.stop_child()
                    self.finish_manual_scan('timeout')
                    await interaction.followup.send('La collecte prend trop de temps. Le dernier point reste disponible avec /cours.',ephemeral=True)
                    return
                try:
                    report = json.loads(output)
                except (ValueError,TypeError):
                    self.finish_manual_scan('failed',reason='invalid_report')
                    await interaction.followup.send('La collecte n’a pas abouti. Le dernier point reste disponible avec /cours.',ephemeral=True)
                    return
                if report.get('status') == 'busy':
                    self.finish_manual_scan('skipped',reason='another_collection_running')
                    await interaction.followup.send('Une autre collecte utilise déjà la session. Voici le dernier point disponible.',ephemeral=True)
                elif self.child.returncode not in (0,2):
                    self.finish_manual_scan('failed',exit_code=self.child.returncode)
                    await interaction.followup.send('La collecte n’a pas abouti. Voici le dernier point disponible.',ephemeral=True)
                else:
                    count = len(report.get('changes',[]))
                    failed = sum(source['status'] != 'ok' for source in report.get('sources',[]))
                    delivery_failed = report.get('delivery', {}).get('status') == 'error'
                    self.finish_manual_scan('partial' if failed or report.get('status') == 'partial' else 'ok',
                        checked_utc=report.get('checked_utc'),sources_ok=len(report.get('sources',[])) - failed,
                        sources_total=len(report.get('sources',[])),changes=count,
                        notifications=report.get('delivery',{}).get('events_delivered',0))
                    message = 'Contrôle terminé : ' + str(count) + ' changement(s) détecté(s). Les nouveautés sont publiées dans le salon habituel.'
                    if delivery_failed:
                        message = 'Contrôle enregistré : ' + str(count) + ' changement(s). Les annonces restent en attente de livraison ; le dernier point est disponible.'
                    if failed:
                        message += ' ' + str(failed) + ' source(s) restent à vérifier ; leurs dates sont les dernières connues.'
                    await interaction.followup.send(message,ephemeral=True)
                await self.send_current(interaction)

    async def stop_child(self):
        if self.child and self.child.returncode is None:
            try:
                os.killpg(self.child.pid,signal.SIGTERM)
                await asyncio.wait_for(self.child.wait(),timeout=10)
            except asyncio.TimeoutError:
                os.killpg(self.child.pid,signal.SIGKILL)
                await self.child.wait()
            except ProcessLookupError:
                pass

    async def close(self):
        if self.retention_task and not self.retention_task.done():
            self.retention_task.cancel()
            await asyncio.gather(self.retention_task, return_exceptions=True)
        if self.history_task and not self.history_task.done():
            self.history_task.cancel()
            await asyncio.gather(self.history_task, return_exceptions=True)
        if self.luna:
            await self.luna.close()
        await self.stop_child()
        await super().close()


def main():
    os.umask(0o077)
    token = load_discord_secrets(SECRETS).get('DISCORD_BOT_TOKEN','')
    if not token:
        raise SystemExit('Bot token not configured')
    client = CourseBot(token)
    client.run(token,log_handler=None)


if __name__ == '__main__':
    main()
