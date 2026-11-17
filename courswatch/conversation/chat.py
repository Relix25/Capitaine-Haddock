"""One bounded asynchronous conversation worker for the configured private channel."""
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import json
import random
import re
import sqlite3
import sys
import time

import discord

from courswatch.discord_app.ui_data import current_events
from courswatch.models import stamp
from courswatch.conversation.easter_egg import triggered
from courswatch.conversation.voice import send_voice_message, VoiceMessageError
from courswatch.conversation.metrics import MeasuredResult, footer, local_metrics, reply_with_footer
from courswatch.conversation.models import MODEL_CHOICES, effective_settings, save_preferences
from courswatch.conversation.brain import (LunaBrain, ChatMemory, ChatCadence, addressed,
                        BudgetExhausted, LunaError, BUDGET_REPLY, OFFLINE_REPLY,
                        REPHRASE_REPLY, REFUSAL_REPLY, RESPONSE_ERRORS, safe_text)


class LunaChat:
    def __init__(self,bot,data,settings,config_loader):
        self.bot,self.data,self.config_loader = bot,data,config_loader
        settings = effective_settings(data,settings)
        self.debug_enabled = settings.get('debug', True)
        if not isinstance(self.debug_enabled, bool):
            raise ValueError('debug must be a boolean')
        self.brain = LunaBrain(data,settings)
        self.model_lock = asyncio.Lock()
        self.memory,self.cadence = ChatMemory(),ChatCadence()
        self.pending_direct = self.pending_ambient = None
        self.task = None
        self.closed = False
        self.sequence = 0
        self.retry_after = 0
        self.clock, self.sleep = time.monotonic, asyncio.sleep

    async def apply_settings(self, selection, debug):
        if (self.brain.provider != 'commandcode' or selection not in MODEL_CHOICES
                or not isinstance(debug, bool)):
            raise ValueError('Unsupported model selection')
        # Let an in-flight response finish under its original model/privacy policy.
        async with self.model_lock:
            await asyncio.to_thread(save_preferences, self.data, selection, debug)
            profile = MODEL_CHOICES[selection]
            self.brain.config = {**self.brain.config, **profile, 'debug':debug}
            self.brain.model = profile['model']
            self.brain.require_zdr = profile['require_zdr']
            self.debug_enabled = debug
            self.retry_after = 0
            try:
                await asyncio.to_thread(self.brain.publish,
                    'daily_limit' if self.brain.budget.snapshot()['blocked'] else 'ready')
            except (OSError, sqlite3.Error):
                pass

    def accept(self,message,mentioned):
        # Caller already enforces guild, channel, role, human and non-webhook checks.
        content = str(message.content or '').strip()
        if not content:
            return
        reference = getattr(message,'reference',None)
        resolved = getattr(reference,'resolved',None)
        reply_bot = bool(reference and (getattr(reference,'message_id',None) in self.memory.bot_ids or
            (self.bot.user and getattr(getattr(resolved,'author',None),'id',None) == self.bot.user.id)))
        direct = addressed(content,mentioned,reply_bot,bare_trigger=triggered(content) and len(content.split()) <= 6)
        if not direct and not message.mentions:
            direct = self.cadence.followup(message.author.id)
        self.memory.add(getattr(message.author,'display_name','Camarade'),content)
        if not direct and (len(content) < 18 or not self.cadence.ambient_allowed()):
            return
        self.sequence += 1
        created = getattr(message,'created_at',None)
        gateway = (max(0,(datetime.now(timezone.utc)-created).total_seconds()*1000)
                   if isinstance(created,datetime) and created.tzinfo is not None else None)
        item = (self.sequence,message,direct,self.clock(),gateway)
        if direct:
            self.pending_direct = item
        else:
            self.pending_ambient = item
        if self.task is None or self.task.done():
            self.task = asyncio.create_task(self.run())

    async def facts(self):
        # Only read cached verified project facts for an explicit course-related question.
        context = self.memory.context()
        recent = ' '.join(row['text'] for row in context[-3:])
        if not re.search(r'\b(cours|projet|deadline|rendu|oral|moodle|teams|aait|aitdm|bdfa|sos|big data)\b',recent,re.I):
            return ''
        try:
            events = await asyncio.to_thread(current_events,self.data,self.config_loader())
            selected = [{key:event[key] for key in ('course','title','body','url') if key in event} for event in events]
            return json.dumps({'lecture_utc':stamp(),'projets_du_cache':selected},ensure_ascii=False)[:7000]
        except (OSError,ValueError,KeyError,sqlite3.Error):
            return 'Cache des cours indisponible. Utiliser /cours ou /actualiser ; ne pas inventer de date.'

    @asynccontextmanager
    async def typing(self,message,direct):
        """A best-effort hint must not delay or replace an actual reply."""
        factory = getattr(getattr(message,'channel',None),'typing',None)
        manager = None
        entered = False
        async def enter():
            nonlocal entered
            await manager.__aenter__()
            entered = True
        try:
            if direct and callable(factory):
                try:
                    manager = factory()
                    await asyncio.wait_for(enter(),timeout=0.25)
                except (TimeoutError,OSError,discord.HTTPException):
                    pass
            yield
        finally:
            if entered:
                try:
                    await manager.__aexit__(*sys.exc_info())
                except (OSError,discord.HTTPException):
                    pass

    def record_timing(self,timing,received,gateway):
        record = {**timing,'total_ms':round(max(0,self.clock()-received)*1000,1),
                  'checked_utc':stamp()}
        if gateway is not None:
            record['gateway_ms'] = round(gateway,1)
        try:
            self.bot.write_status(last_chat_timing=record)
        except OSError:
            # Diagnostics must not turn a delivered reply into a callback failure.
            pass

    async def run(self):
        try:
            while not self.closed and (self.pending_direct or self.pending_ambient):
                item = self.pending_direct or self.pending_ambient
                sequence,message,direct,received,gateway = item
                delay = max((0.15 if direct else 4) - (self.clock()-received),
                            2-(self.cadence.clock()-self.cadence.last_sent),0)
                if delay:
                    await self.sleep(delay)
                # Coalesce rapid messages, prioritising the latest direct request.
                if item is not (self.pending_direct or self.pending_ambient):
                    continue
                if direct:
                    self.pending_direct = self.pending_ambient = None
                else:
                    self.pending_ambient = None
                    if not self.cadence.ambient_allowed():
                        continue
                self.cadence.last_analysis = self.cadence.clock()
                timing = {'mode':'direct' if direct else 'ambient',
                          'queue_ms':round(max(0,self.clock()-received)*1000,1),
                          'model_ms':0.0,'delivery_ms':0.0}
                async def deliver(decision,is_direct,conversation=True):
                    started = self.clock()
                    try:
                        await self.deliver(message,decision,is_direct,conversation)
                    finally:
                        timing['delivery_ms'] += round(max(0,self.clock()-started)*1000,1)
                try:
                    if self.clock() < self.retry_after:
                        if direct:
                            await deliver(MeasuredResult({'action':'text','reply':OFFLINE_REPLY,'sample_id':'none'},local_metrics()),True,False)
                        continue
                    try:
                        async with self.typing(message,direct):
                            facts = await self.facts() if direct else ''
                            started = self.clock()
                            try:
                                async with self.model_lock:
                                    decision = await asyncio.to_thread(self.brain.decide,self.memory.context(),direct,
                                                                       self.bot.voice_samples,facts)
                            finally:
                                timing['model_ms'] = round(max(0,self.clock()-started)*1000,1)
                    except BudgetExhausted:
                        if direct or self.brain.budget.first_notice():
                            await deliver(MeasuredResult({'action':'text','reply':BUDGET_REPLY,'sample_id':'none'},local_metrics()),direct,False)
                        continue
                    except LunaError as error:
                        response_problem = error.code in RESPONSE_ERRORS
                        if not response_problem:
                            self.retry_after = self.clock()+60
                        self.bot.write_status(last_luna_error={'code':error.code,'checked_utc':stamp(),
                            'scope':'message' if response_problem else 'service'})
                        if direct:
                            fallback = (REFUSAL_REPLY if error.code == 'model_refusal' else REPHRASE_REPLY) if response_problem else OFFLINE_REPLY
                            await deliver(MeasuredResult({'action':'text','reply':fallback,'sample_id':'none'},error.metrics),True,False)
                        continue
                    # A direct message arriving during ambient analysis takes precedence over the joke.
                    if not direct and self.pending_direct:
                        continue
                    metrics = getattr(decision,'metrics',{})
                    for key in ('ttft_ms','api_ms','input_tokens','output_tokens','total_tokens','cached_input_tokens','output_tps'):
                        if isinstance(metrics.get(key),(int,float)):
                            timing[key] = round(metrics[key],2)
                    if decision['action'] != 'ignore':
                        await deliver(decision,direct)
                finally:
                    self.record_timing(timing,received,gateway)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            # No exception strings or message data in persistent diagnostics.
            self.bot.write_status(last_luna_callback_error={'error_type':type(error).__name__,'checked_utc':stamp()})

    async def deliver(self,message,decision,direct,conversation=True):
        debug = self.debug_enabled
        metrics = getattr(decision,'metrics',{})
        if not metrics and not conversation:
            metrics = local_metrics()
        sample = next((sample for sample in self.bot.voice_samples if sample.id == decision['sample_id']),None)
        reply_id,format_used = None,'text'
        if (decision['action'] == 'sample' and sample and sample.id != self.bot.last_voice_sample
                and random.random() < self.bot.voice_probability):
            try:
                receipt = await asyncio.to_thread(send_voice_message,str(message.channel.id),self.bot.token_value,
                    sample,nonce='hl'+str(message.id),reference_id=str(message.id))
                reply_id,format_used = int(receipt['id']),'voice'
                self.bot.last_voice_sample = sample.id
            except (VoiceMessageError,OSError,ValueError) as error:
                self.bot.write_status(last_voice_error={'error_type':type(error).__name__,'checked_utc':stamp()})
        if reply_id is None:
            try:
                content = safe_text(decision['reply'])
                if debug:
                    content = reply_with_footer(content,metrics,local_response=not conversation)
                reply = await message.reply(content,mention_author=False,
                                            allowed_mentions=discord.AllowedMentions.none())
                reply_id = reply.id
            except discord.HTTPException:
                self.bot.write_status(last_luna_delivery_error=stamp())
                return
        elif debug:
            # Native Discord voice messages reject text content. Keep their
            # measurements in a small reply to that audio, without a mention.
            try:
                await message.channel.send(footer(metrics,local_response=not conversation),
                    reference=discord.MessageReference(message_id=reply_id,channel_id=message.channel.id,fail_if_not_exists=False),
                    mention_author=False,allowed_mentions=discord.AllowedMentions.none())
            except discord.HTTPException as error:
                self.bot.write_status(last_chat_metrics_error={'error_type':type(error).__name__,'checked_utc':stamp()})
        self.memory.add('Haddock',('[Extrait vocal : '+sample.id+']' if format_used == 'voice' else decision['reply']),
                        reply_id,is_bot=True)
        self.cadence.sent(direct,message.author.id,conversation)
        self.bot.write_status(last_luna_reply={'sent_utc':stamp(),'reply_id':str(reply_id),
            'mode':'direct' if direct else 'ambient','format':format_used},last_luna_delivery_error=None)

    async def close(self):
        self.closed = True
        self.pending_direct = self.pending_ambient = None
        if self.task and not self.task.done():
            self.task.cancel()
            await asyncio.gather(self.task,return_exceptions=True)
        self.memory.rows.clear()
        self.memory.bot_ids.clear()
