#!/usr/bin/env python3
"""Discord commands and private detail buttons; outbound Gateway only."""
import asyncio
from datetime import datetime, timezone
import json
import os
import random
import signal
import sqlite3
import sys
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
from courswatch.discord_app.presentation import compact_embed, interaction_allowed, private_details, DETAIL_CUSTOM_ID
from courswatch.discord_app.responses import prepare_private, send_private

DATA = Path(os.environ.get('COURS_WATCH_DATA','/var/lib/cours-watch'))
CONFIG = Path(os.environ.get('COURS_WATCH_CONFIG','/etc/cours-watch/config.json'))
SECRETS = Path(os.environ.get('COURS_WATCH_SECRETS','/etc/cours-watch/secrets.json'))


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
        self.child = None
        self.commands_registered = False
        self.register_commands()

    def write_status(self, **extra):
        path = DATA / 'reports/bot-status.json'
        previous = read_json(path)
        previous.update(extra)
        previous['process_id'] = os.getpid()
        previous['checked_utc'] = datetime.now(timezone.utc).isoformat(timespec='seconds')
        path.parent.mkdir(mode=0o700,exist_ok=True)
        pending = path.with_suffix('.pending')
        pending.write_text(json.dumps(previous,ensure_ascii=False,indent=2))
        pending.chmod(0o600)
        os.replace(pending,path)

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
        self.commands_registered = True

    async def on_ready(self):
        cfg = configuration()['discord']
        guild = self.get_guild(int(cfg['guild_id']))
        channel = guild.get_channel(int(cfg['channel_id'])) if guild else None
        permissions = channel.permissions_for(guild.me) if channel and guild.me else None
        usable = bool(permissions and permissions.view_channel and permissions.send_messages and permissions.embed_links)
        role = guild.get_role(int(cfg['role_ids'][0])) if guild else None
        ping = bool(role and permissions and (role.mentionable or permissions.mention_everyone))
        self.write_status(ready=bool(usable and self.commands_registered), application_id=str(self.application_id),
                          commands=[command.name for command in self.tree.get_commands(guild=self.scope)], private_buttons=True, channel_access=usable, role_ping_allowed=ping,
                          easter_egg_enabled=self.easter_egg_enabled,message_content_intent=self.intents.message_content,
                          voice_samples=len(self.voice_samples),voice_probability=self.voice_probability,
                          luna_enabled=self.luna_enabled,luna_model=self.luna.brain.model if self.luna else None,
                          luna_provider=self.luna.brain.provider if self.luna else None,
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

    async def on_message(self,message):
        if (not (self.easter_egg_enabled or self.luna_enabled) or message.guild is None or message.author.bot
                or getattr(message,'webhook_id',None)):
            return
        cfg = configuration()['discord']
        roles = [role.id for role in getattr(message.author,'roles',[])]
        if not interaction_allowed(message.guild.id,message.channel.id,roles,cfg):
            return
        mentioned = bool(self.user and any(user.id == self.user.id for user in message.mentions))
        if self.luna:
            self.luna.accept(message,mentioned)
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
                    self.child = await asyncio.create_subprocess_exec(*scan_command(DATA, CONFIG),stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE,
                        start_new_session=True)
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
