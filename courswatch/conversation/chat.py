"""Scoped conversation orchestration with attributed, versioned FIFO requests."""
import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import datetime, timezone
import json
import hashlib
import re
from pathlib import Path
import random
import sqlite3
import sys
import time
from zoneinfo import ZoneInfo

import discord

from courswatch.models import stamp
from courswatch.conversation.voice import send_voice_message, VoiceMessageError
from courswatch.conversation.metrics import MeasuredResult, footer, local_metrics, reply_with_footer
from courswatch.conversation.models import MODEL_CHOICES, effective_settings, save_preferences
from courswatch.conversation.brain import (LunaBrain, ChatMemory, ChatCadence, addressed,
                        BudgetExhausted, LunaError, BUDGET_REPLY, OFFLINE_REPLY,
                        REPHRASE_REPLY, REFUSAL_REPLY, RESPONSE_ERRORS, safe_text)
from courswatch.conversation.contracts import MessageEnvelope, Scope, utc_iso, Evidence, EvidenceBundle
from courswatch.conversation.coordinator import Coordinator, QueueFull
from courswatch.conversation.participation import decide as participation, CANCEL, CONTINUATION


class LunaChat:
    def __init__(self,bot,data,settings,config_loader,*,journal=None,knowledge=None,media=None,brain=None,now=None):
        self.bot,self.data,self.config_loader = bot,Path(data),config_loader
        settings = effective_settings(data,settings)
        self.settings = settings
        self.now=now or (lambda:datetime.now(timezone.utc))
        self.debug_enabled = settings.get('debug', True)
        if not isinstance(self.debug_enabled, bool):
            raise ValueError('debug must be a boolean')
        self.brain = brain if brain is not None else LunaBrain(data,settings)
        self.model_lock = asyncio.Lock()
        self.publication_lock=asyncio.Lock()
        self.memory,self.cadence = ChatMemory(),ChatCadence()
        self.clock, self.sleep = time.monotonic, asyncio.sleep
        self.coordinator = Coordinator(max_pending=settings.get('max_pending_turns',128),
            max_age=settings.get('turn_max_age_seconds',120),clock=lambda:self.clock())
        if journal is None:
            from courswatch.memory import Journal
            journal = Journal(self.data/'conversation.db',retention_days=settings.get('memory',{}).get('retention_days',90))
        self.journal = journal
        if knowledge is None:
            from courswatch.knowledge import KnowledgeService
            knowledge = KnowledgeService(self.data,self.config_loader,journal=self.journal if self.journal is not False else None,
                allowed_sources=lambda:self.config_loader().get('discord',{}).get('luna',{}).get('knowledge',{}).get('allowed_sources',{}))
        self.knowledge,self.media = knowledge,media
        self._local_answers = {}
        self._media_timings = {}
        self._overflow_task = None
        self._messages,self._ingest_ms,self._evidence = {},{},{}
        self._warm_scopes = set()
        self._wake = asyncio.Event()
        self.task = None
        self.closed = False
        self.retry_after = 0

    async def apply_settings(self, selection, debug):
        if (self.brain.provider != 'commandcode' or selection not in MODEL_CHOICES
                or not isinstance(debug, bool)):
            raise ValueError('Unsupported model selection')
        async with self.model_lock:
            await asyncio.to_thread(save_preferences, self.data, selection, debug)
            profile = MODEL_CHOICES[selection]
            self.brain.config = {**self.brain.config, **profile, 'debug':debug}
            self.brain.model = profile['model']
            self.brain.require_zdr = profile['require_zdr']
            self.debug_enabled = debug
            try:
                await asyncio.to_thread(self.brain.publish,
                    'daily_limit' if self.brain.budget.snapshot()['blocked'] else 'ready')
            except (OSError, sqlite3.Error):
                pass

    def accept(self,message,mentioned=False):
        # Authorization precedes normalization, persistence and media acquisition.
        from courswatch.conversation.ingestion import envelope_from_message
        envelope = envelope_from_message(message,now=self.now())
        reference=getattr(message,'reference',None)
        resolved=getattr(reference,'resolved',None)
        source_channel=getattr(reference,'channel_id',None) or message.channel.id
        source_guild=getattr(reference,'guild_id',None) or message.guild.id
        resolved_bot=(str(getattr(getattr(resolved,'author',None),'id',''))==str(getattr(getattr(self.bot,'user',None),'id','bot'))
                      and str(source_channel)==str(message.channel.id) and str(source_guild)==str(message.guild.id))
        return self.observe(envelope,message,mentioned or resolved_bot)

    def _persist(self,envelope):
        if self.journal is False:
            return envelope
        return self.journal.ingest(envelope,backfill=envelope.ingestion_mode != 'live')

    def observe(self,envelope,message=None,mentioned=False):
        started = self.clock()
        if self.closed:
            return None
        if envelope.scope.key not in self._warm_scopes:
            if self.journal is not False:
                for old in self.journal.recent(envelope.scope,limit=30):
                    self.coordinator.observe(old)
            self._warm_scopes.add(envelope.scope.key)
        ancestor_id=envelope.reply_to_message_id
        if self.journal is not False:
            for _ in range(8):
                if not ancestor_id:
                    break
                ancestor=self.journal.active(envelope.scope,ancestor_id)
                if ancestor is None:
                    break
                self.coordinator.observe(ancestor)
                ancestor_id=ancestor.reply_to_message_id
        saved = self._persist(envelope)
        if saved is None or not self.coordinator.observe(saved):
            return None
        envelope = saved
        key=(envelope.scope.key,envelope.message_id)
        self._ingest_ms[key] = max(0,self.clock()-started)*1000
        self._prune_transports()
        if message is not None:
            self._messages[key] = message
        self.memory.add(envelope.display_name,envelope.text,envelope.message_id,is_bot=envelope.is_bot)
        if self.media is not None and envelope.media:
            # Media services retain their own bounded queue; they never publish.
            self.media.submit(envelope)
        bot_id = str(getattr(getattr(self.bot,'user',None),'id',''))
        reply_bot = (envelope.reply_to_message_id is not None and
            (str(envelope.reply_to_message_id) in {str(identity) for identity in self.memory.bot_ids}
             or self.coordinator.bot_links.get((envelope.scope.key,envelope.reply_to_message_id)) is not None
             or (self.coordinator.active(envelope.scope,envelope.reply_to_message_id) is not None
                 and self.coordinator.active(envelope.scope,envelope.reply_to_message_id).is_bot)))
        direct = addressed(envelope.text,mentioned or bot_id in envelope.mentioned_user_ids,reply_bot)
        policy = participation(envelope,direct=direct,ambient_allowed=self.cadence.ambient_allowed(),
                               mode=self.settings.get('participation_mode','selective'))
        self.bot.write_status(last_chat_decision={'decision':policy.decision,'reason_code':policy.reason_code,'checked_utc':stamp()})
        if envelope.ingestion_mode != 'live' or envelope.is_bot:
            return policy
        # Source timestamps, not replay receipt timestamps, bound live validity.
        try:
            age=(self.now()-datetime.fromisoformat(envelope.created_at.replace('Z','+00:00'))).total_seconds()
            if age > self.coordinator.max_age:
                return replace(policy,decision='silence',reason_code='expired_request')
        except (ValueError,TypeError):
            return replace(policy,decision='silence',reason_code='invalid_source_time')
        linked = self.coordinator._linked_turn(envelope)
        continuation = CONTINUATION.search(envelope.text)
        if policy.decision == 'silence' and not (linked or CANCEL.search(envelope.text) or continuation or self.coordinator.can_attach_fragment(envelope)):
            return policy
        gateway=max(0,age*1000)
        try:
            turn=self.coordinator.enqueue(envelope,direct,wait_ms=policy.recheck_after_ms,gateway_ms=gateway)
        except QueueFull:
            self.bot.write_status(last_chat_decision={'decision':'silence','reason_code':'queue_full','checked_utc':stamp()})
            if direct and message is not None and (self._overflow_task is None or self._overflow_task.done()):
                self._overflow_task=asyncio.create_task(self._overload_reply(message))
            return replace(policy,decision='silence',reason_code='queue_full')
        self._wake.set()
        if turn is not None and (self.task is None or self.task.done()):
            self.task=asyncio.create_task(self.run())
        return policy

    def _prune_transports(self):
        protected={(turn.scope.key,identity) for turn in self.coordinator.turns.values()
                   if turn.state in ('queued','generating') for identity in turn.message_ids}
        for mapping in (self._messages,self._ingest_ms):
            for key in tuple(mapping)[:-512]:
                if key not in protected:
                    mapping.pop(key,None)

    def edited(self,envelope):
        saved=self._persist(envelope)
        if saved is not None:
            self.coordinator.observe(saved)
            if self.media is not None and saved.media:
                self.media.submit(saved)
            self._wake.set()
        return saved

    def deleted(self,scope,message_id):
        if self.journal is not False:
            self.journal.delete(scope,str(message_id))
        self.coordinator.delete(scope,str(message_id))
        self._wake.set()

    def backfill(self,envelopes):
        return [self.observe(replace(envelope,ingestion_mode='backfill')) for envelope in envelopes]

    async def _overload_reply(self,message):
        # One bounded local overload notice: no paid call or hidden extra turn.
        try:
            async with self.publication_lock:
                delay=max(0,2-(self.cadence.clock()-self.cadence.last_sent))
                if delay:
                    await self.sleep(delay)
                allowed=getattr(self.bot,'conversation_publish_allowed',None)
                if callable(allowed) and not await allowed(message):
                    return
                await self.deliver(message,MeasuredResult({'action':'text','reply':
                    'Mille sabords ! Trop de demandes sont déjà en attente. Réessaie dans un instant.',
                    'sample_id':'none'},local_metrics()),True,False)
        except (OSError,discord.HTTPException):
            pass

    async def facts(self,snapshot,profile=None):
        self._local_answers.pop(snapshot.turn_id,None)
        self._evidence.pop(snapshot.turn_id,None)
        self._media_timings.pop(snapshot.turn_id,None)
        help_lookup=getattr(self.bot,'conversation_command_help',None)
        command_help=help_lookup(snapshot.goal) if callable(help_lookup) else ''
        if command_help:
            self._local_answers[snapshot.turn_id]=command_help
            return command_help
        media_messages=[item for item in snapshot.messages if item.media]
        if media_messages:
            observations=[]
            if self.media is not None:
                resolver=getattr(self.brain,'vision_profile',None)
                vision_profile=resolver(profile) if callable(resolver) else profile
                for item in media_messages:
                    observations.extend(await self.media.enrich(item,profile=vision_profile))
            measured={name:round(sum(row.get('metrics',{}).get(name,0) for row in observations
                if isinstance(row.get('metrics',{}).get(name,0),(int,float))),2)
                for name in ('media_fetch_ms','media_decode_ms','vision_ms')}
            measured.update(media_items=len(observations),media_cache_hits=sum(bool(row.get('cache_hit')) for row in observations),
                            media_ready=sum(row.get('status')=='ready' for row in observations))
            self._media_timings[snapshot.turn_id]=measured
            lines=[]
            proofs=[]
            for item in observations:
                if item.get('status')!='ready':
                    continue
                source_message=next((message for message in media_messages if message.message_id==str(item.get('message_id'))),None)
                if source_message is None:
                    continue
                description=str(item.get('description','')).strip()
                ocr=[]
                qualities={'readable':'lisible','uncertain':'incertain','partial':'partiel','unreadable':'illisible'}
                for row in item.get('ocr',()):
                    if isinstance(row,dict):
                        text=str(row.get('text',''))
                        at=row.get('at_ms')
                        moment=(' à '+format(at/1000,'.2f').replace('.',',')+' s') if isinstance(at,(int,float)) else ''
                        quality=qualities.get(row.get('quality'),'qualité inconnue')
                        if text:
                            ocr.append(text+' ('+quality+moment+')')
                    elif isinstance(row,str):
                        ocr.append(row)
                animation=str(item.get('animation','')).strip()
                limitations=' ; '.join(str(value) for value in item.get('limitations',()) if value)
                details=' — '.join(value for value in (description,'Texte lu : '+' ; '.join(ocr) if ocr else '',animation,
                    'Limites : '+limitations if limitations else '') if value)
                if not details:
                    continue
                def dated(value):
                    try:
                        return datetime.fromisoformat(value.replace('Z','+00:00')).astimezone(ZoneInfo('Europe/Bucharest')).strftime('%d/%m/%Y à %H:%M')
                    except (AttributeError,ValueError):
                        return 'date inconnue'
                analysis_at=item.get('analysed_at') or item.get('analyzed_at')
                prefix=('Observation antérieure, média non revérifié' if item.get('prior_observation') else 'Observation du média')
                prefix+=' envoyé par '+source_message.display_name+' le '+dated(source_message.created_at)
                prefix+=' (analyse du '+dated(analysis_at)+') : '
                source=source_message.source_url
                line=prefix+details+(' [Image](<'+source+'>)' if source.startswith('https://discord.com/channels/') else '')
                if len('\n'.join([*lines,line]))>1700:
                    continue
                lines.append(line)
                canonical=item
                media_id=str(item.get('media_id',''))
                if self.journal is not False:
                    current=self.journal.observations(snapshot.scope,source_message.message_id,media_id)
                    canonical=next((row['observation'] for row in current if row['observation'].get('status')=='ready'),item)
                observation_hash=hashlib.sha256(json.dumps(canonical,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
                proofs.append(Evidence('media:'+source_message.message_id+':'+media_id,'media_observation',
                    snapshot.scope,json.dumps([source_message.message_id,media_id]),source_message.source_url,
                    source_message.display_name,details,str(source_message.revision)+':'+observation_hash,
                    analysis_at or source_message.received_at or source_message.created_at,
                    source_message.created_at,source_message.author_id,status='observation'))
            ready=any(item.get('status')=='ready' for item in observations)
            unavailable='Je ne peux pas lire ce média avec les capacités actuellement disponibles. Décris-moi ce que tu veux vérifier.'
            oversized='Une observation est mémorisée, mais elle est trop longue pour un seul message. Quel détail veux-tu vérifier ?'
            result='\n'.join(lines) if lines else (oversized if ready else unavailable)
            if proofs:
                self._evidence[snapshot.turn_id]=EvidenceBundle(evidence=tuple(proofs))
            self._local_answers[snapshot.turn_id]=result
            return result
        if self.knowledge is not False:
            try:
                bundle=await asyncio.to_thread(self.knowledge.query,snapshot.scope,snapshot.goal,budget_chars=7000)
            except (OSError,ValueError,sqlite3.Error):
                result='La recherche des sources est indisponible pour le moment. Je ne peux pas confirmer cette information.'
                self._local_answers[snapshot.turn_id]=result
                return result
            self._evidence[snapshot.turn_id]=bundle
            if bundle.evidence or bundle.clarification or (bundle.coverage and re.search(
                    r'\b(aait|aitdm|bdfa|sos|cours|projet|deadline|rendu|quand|date|souviens|souvenir|image|gif|emoji)\b',snapshot.goal,re.I)):
                from courswatch.knowledge import render_answer
                result=render_answer(bundle)
                if len(result)>1700:
                    chosen=[]
                    for proof in bundle.evidence:
                        candidate=replace(bundle,evidence=tuple([*chosen,proof]))
                        if len(render_answer(candidate))<=1700:
                            chosen.append(proof)
                    bundle=replace(bundle,evidence=tuple(chosen))
                    self._evidence[snapshot.turn_id]=bundle
                    result=render_answer(bundle) if chosen else 'Les preuves sont trop longues pour une réponse unique. Précise le projet ou le passage que tu veux vérifier.'
                self._local_answers[snapshot.turn_id]=result
                return result
        if self.journal is False:
            return ''
        memories=await asyncio.to_thread(self.journal.search,snapshot.scope,snapshot.goal,limit=8)
        # Whole evidence records are selected; never cut serialized JSON mid-source.
        selected=[]
        proofs=[]
        for item in memories:
            if item.is_bot or item.message_id in snapshot.trigger_message_ids:
                continue
            row=dict(author_id=item.author_id,speaker=item.display_name,text=item.text,
                message_id=item.message_id,created_at=item.created_at,revision=item.revision,source_url=item.source_url)
            if len(json.dumps([*selected,row],ensure_ascii=False)) <= 6000:
                selected.append(row)
                proofs.append(Evidence('message:'+item.message_id,'conversation',snapshot.scope,
                    item.message_id,item.source_url,item.display_name,item.text,str(item.revision),
                    item.received_at or item.created_at,item.created_at,item.author_id))
        if proofs:
            # Every retrieved row is a publication dependency, including social
            # recall that did not match an explicit history intent.
            self._evidence[snapshot.turn_id]=EvidenceBundle(evidence=tuple(proofs))
        return json.dumps({'souvenirs_attribues':selected},ensure_ascii=False) if selected else ''

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

    async def _wait(self,delay):
        delay=round(delay,9)
        if self.sleep is not asyncio.sleep:
            await self.sleep(delay)
            return
        self._wake.clear()
        sleeper=asyncio.create_task(self.sleep(delay))
        wake=asyncio.create_task(self._wake.wait())
        try:
            await asyncio.wait((sleeper,wake),return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in (sleeper,wake):
                if not task.done():
                    task.cancel()
            await asyncio.gather(sleeper,wake,return_exceptions=True)

    async def _valid(self,snapshot,message,bundle=None):
        if not self.coordinator.valid(snapshot):
            return False
        if self.journal is not False:
            for identity,revision in snapshot.dependencies:
                active=self.journal.active(snapshot.scope,identity)
                if active is None or active.deleted or active.revision != revision:
                    return False
        if bundle is not None and self.knowledge is not False:
            if not await asyncio.to_thread(self.knowledge.revalidate,snapshot.scope,bundle):
                return False
        evidence_allowed=getattr(self.bot,'conversation_evidence_allowed',None)
        if bundle is not None and bundle.evidence and callable(evidence_allowed) and not await evidence_allowed(bundle):
            return False
        allowed=getattr(self.bot,'conversation_publish_allowed',None)
        if callable(allowed) and not await allowed(message):
            return False
        # Asynchronous permission/source checks can themselves receive a correction.
        if not self.coordinator.valid(snapshot):
            return False
        if self.journal is not False:
            for identity,revision in snapshot.dependencies:
                active=self.journal.active(snapshot.scope,identity)
                if active is None or active.deleted or active.revision!=revision:
                    return False
        message_allowed=getattr(self.bot,'conversation_message_allowed',None)
        if callable(message_allowed) and not message_allowed(message):
            return False
        # Final source/access validation is local and synchronous: no await can
        # admit a revocation between this check and the outbound commitment.
        if bundle is not None and self.knowledge is not False and not self.knowledge.revalidate(snapshot.scope,bundle):
            return False
        return True

    async def _model(self,snapshot,direct,facts,timing,profile=None):
        queued=self.clock()
        async with self.model_lock:
            timing['model_lock_ms']=round(max(0,self.clock()-queued)*1000,1)
            started=self.clock()
            worker=asyncio.create_task(asyncio.to_thread(self.brain.decide,snapshot.as_context(),direct,
                self.bot.voice_samples,facts,request=snapshot,profile=profile))
            try:
                # Cancelling a coroutine does not terminate urllib in its worker thread.
                # Track it until settlement before allowing this service to close.
                return await asyncio.shield(worker)
            except asyncio.CancelledError:
                await asyncio.gather(worker,return_exceptions=True)
                raise
            finally:
                timing['model_ms']=round(max(0,self.clock()-started)*1000,1)

    async def run(self):
        try:
            while not self.closed and (turn:=self.coordinator.next()) is not None:
                delay=max(turn.ready_at-self.clock(),2-(self.cadence.clock()-self.cadence.last_sent),0)
                if delay:
                    await self._wait(delay)
                    continue
                if not turn.direct and not self.cadence.ambient_allowed():
                    self.coordinator.finish(turn,'silence')
                    continue
                message=self._messages.get((turn.scope.key,turn.reply_target_id))
                if message is None:
                    self.coordinator.finish(turn,'unavailable_target')
                    continue
                snapshot=self.coordinator.freeze(turn)
                turn.state='generating'
                turn.attempts+=1
                self.cadence.last_analysis=self.cadence.clock()
                key=(turn.scope.key,turn.trigger_ids[0])
                timing={'mode':'direct' if turn.direct else 'ambient','ingest_ms':round(self._ingest_ms.get(key,0),1),
                    'debounce_ms':round(max(0,turn.ready_at-turn.opened_at)*1000,1),
                    'queue_ms':round(max(0,self.clock()-turn.opened_at)*1000,1),'retrieval_ms':0.0,
                    'model_lock_ms':0.0,'model_ms':0.0,'validation_ms':0.0,'delivery_ms':0.0}
                try:
                    conversation=True
                    if self.clock()<self.retry_after and not turn.direct:
                        self.coordinator.finish(turn,'silence')
                        continue
                    try:
                        async with self.typing(message,turn.direct):
                            capture=getattr(self.brain,'capture_profile',None)
                            profile=capture() if callable(capture) else None
                            started=self.clock()
                            facts=await self.facts(snapshot,profile=profile) if turn.direct else ''
                            timing['retrieval_ms']=round(max(0,self.clock()-started)*1000,1)
                            timing.update(self._media_timings.pop(turn.turn_id,{}))
                            bundle=self._evidence.get(turn.turn_id)
                            if turn.turn_id in self._local_answers:
                                decision=MeasuredResult({'action':'text','reply':facts,'sample_id':'none'},local_metrics())
                                conversation=False
                            else:
                                can_call=getattr(self.brain,'can_call',None)
                                if self.clock()<self.retry_after or (callable(can_call) and not can_call()):
                                    if not turn.direct:
                                        self.coordinator.finish(turn,'silence')
                                        continue
                                    decision=MeasuredResult({'action':'text','reply':OFFLINE_REPLY,'sample_id':'none'},local_metrics())
                                    conversation=False
                                else:
                                    decision=await self._model(snapshot,turn.direct,facts,timing,profile=profile)
                    except BudgetExhausted:
                        if not turn.direct:
                            self.coordinator.finish(turn,'silence')
                            continue
                        budget=getattr(self.brain,'budget',None)
                        state=budget.snapshot() if budget is not None else {'blocked':True}
                        if state.get('blocked',False):
                            reply=BUDGET_REPLY
                        elif state.get('remaining_tokens',0)<=0:
                            reply='Des appels en cours mobilisent la provision disponible. Réessaie quand leur consommation aura été confirmée.'
                        else:
                            reply='Cette demande dépasse le budget disponible pour un appel. Précise le passage utile ou réduis la demande.'
                        decision=MeasuredResult({'action':'text','reply':reply,'sample_id':'none'},local_metrics())
                        conversation=False
                    except LunaError as error:
                        response_problem=error.code in RESPONSE_ERRORS or error.code in ('capability_unavailable','input_limit','invalid_media','policy_mismatch','payload_too_large','http_400','http_422')
                        if not response_problem and error.code not in ('circuit_open','service_circuit_open'):
                            self.retry_after=self.clock()+60
                        self.bot.write_status(last_luna_error={'code':error.code,'checked_utc':stamp(),
                            'scope':'message' if response_problem else 'service'})
                        if not turn.direct:
                            self.coordinator.finish(turn,'silence')
                            continue
                        fallback=(REFUSAL_REPLY if error.code=='model_refusal' else REPHRASE_REPLY) if response_problem else OFFLINE_REPLY
                        decision=MeasuredResult({'action':'text','reply':fallback,'sample_id':'none'},error.metrics)
                        conversation=False
                    async with self.publication_lock:
                        started=self.clock()
                        valid=await self._valid(snapshot,message,self._evidence.get(turn.turn_id))
                        timing['validation_ms']=round(max(0,self.clock()-started)*1000,1)
                        if not valid:
                            # Relevant revisions retry once; resolved/deleted/cancelled turns stay silent.
                            retried=self.coordinator.retry(turn)
                            timing['discarded_request']=True
                            timing['requeued_request']=retried
                            continue
                        if not turn.direct and self.coordinator.pending_direct:
                            self.coordinator.finish(turn,'silence')
                            continue
                        if decision['action']=='ignore':
                            self.coordinator.finish(turn,'silence')
                            continue
                        for name,value in getattr(decision,'metrics',{}).items():
                            if name in ('ttft_ms','api_ms','input_tokens','output_tokens','total_tokens','cached_input_tokens','output_tps') and isinstance(value,(int,float)):
                                timing[name]=round(value,2)
                            elif name in ('model_profile_id','provider','requested_model_id','resolved_model_id',
                                          'resolved_model_revision','profile_config_version','require_zdr','unknown_usage'):
                                timing[name]=value
                        dedupe=hashlib.sha256(json.dumps([snapshot.scope.key,snapshot.trigger_message_ids,
                            snapshot.reply_target_id,snapshot.dependencies,snapshot.goal],ensure_ascii=False).encode()).hexdigest()
                        if self.journal is not False:
                            if not self.journal.prepare_outbound(snapshot,dedupe) or not self.journal.commit_outbound(dedupe):
                                self.coordinator.finish(turn,'uncertain')
                                continue
                        started=self.clock()
                        try:
                            receipt=await self.deliver(message,decision,turn.direct,conversation)
                        except BaseException:
                            known_receipt=getattr(self,'_last_transport_receipt',None)
                            if self.journal is not False:
                                self.journal.mark_outbound(dedupe,'sent' if known_receipt else 'uncertain',str(known_receipt) if known_receipt else None)
                            self.coordinator.finish(turn,'completed' if known_receipt else 'uncertain',known_receipt)
                            raise
                        timing['delivery_ms']=round(max(0,self.clock()-started)*1000,1)
                        if receipt is not None:
                            timing['first_useful_ms']=round(max(0,getattr(self,'_last_delivery_useful_at',self.clock())-turn.received_at)*1000,1)
                        if self.journal is not False:
                            self.journal.mark_outbound(dedupe,'sent' if receipt is not None else 'uncertain',str(receipt) if receipt is not None else None)
                        changed_during_send=not self.coordinator.valid(snapshot)
                        if receipt is not None and changed_during_send:
                            self.bot.write_status(last_chat_publication={'state':'changed_during_send','reply_id':str(receipt),'checked_utc':stamp()})
                        self.coordinator.finish(turn,'completed' if receipt is not None else 'uncertain',receipt)
                        self._evidence.pop(turn.turn_id,None)
                        self._local_answers.pop(turn.turn_id,None)
                        for transport_key in tuple(self._messages)[:-512]:
                            if transport_key not in {(item.scope.key,item.reply_target_id) for item in self.coordinator.turns.values() if item.state in ('queued','generating')}:
                                self._messages.pop(transport_key,None)
                                self._ingest_ms.pop(transport_key,None)
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    self.coordinator.finish(turn,'failed')
                    self.bot.write_status(last_luna_callback_error={'error_type':type(error).__name__,'checked_utc':stamp()})
                finally:
                    self._media_timings.pop(turn.turn_id,None)
                    if turn.state not in ('queued','generating'):
                        self._evidence.pop(turn.turn_id,None)
                        self._local_answers.pop(turn.turn_id,None)
                    self._prune_transports()
                    self.record_timing(timing,turn.received_at,turn.gateway_ms)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            self.bot.write_status(last_luna_callback_error={'error_type':type(error).__name__,'checked_utc':stamp()})

    async def deliver(self,message,decision,direct,conversation=True):
        self._last_transport_receipt=None
        debug = self.debug_enabled
        metrics = getattr(decision,'metrics',{})
        if not metrics and not conversation:
            metrics = local_metrics()
        sample = next((sample for sample in self.bot.voice_samples if sample.id == decision['sample_id']),None)
        reply_id,format_used = None,'text'
        if (decision['action'] == 'sample' and sample and sample.id != self.bot.last_voice_sample
                and random.random() < self.bot.voice_probability):
            try:
                voice_worker=asyncio.create_task(asyncio.to_thread(send_voice_message,str(message.channel.id),self.bot.token_value,
                    sample,nonce='hl'+str(message.id),reference_id=str(message.id)))
                try:
                    receipt=await asyncio.shield(voice_worker)
                except asyncio.CancelledError:
                    result=await asyncio.gather(voice_worker,return_exceptions=True)
                    if result and isinstance(result[0],dict) and result[0].get('id'):
                        self._last_transport_receipt=int(result[0]['id'])
                    raise
                reply_id,format_used = int(receipt['id']),'voice'
                self._last_transport_receipt=reply_id
                self._last_delivery_useful_at=self.clock()
                self.bot.last_voice_sample = sample.id
            except (VoiceMessageError,OSError,ValueError) as error:
                # A missing/ambiguous receipt may still correspond to a delivered
                # voice message. Only a known local validation or 4xx rejection
                # permits a text fallback; never duplicate an uncertain upload.
                definite=isinstance(error,ValueError) or bool(re.fullmatch(r'Discord voice HTTP 4\d\d|Discord voice rate limit',str(error)))
                self.bot.write_status(last_voice_error={'error_type':type(error).__name__,
                    'outcome':'rejected' if definite else 'uncertain','checked_utc':stamp()})
                if not definite:
                    return None
        if reply_id is None:
            try:
                content = safe_text(decision['reply'])
                if debug:
                    content = reply_with_footer(content,metrics,local_response=not conversation)
                reply = await message.reply(content,mention_author=False,
                                            allowed_mentions=discord.AllowedMentions.none())
                reply_id = reply.id
                self._last_transport_receipt=reply_id
                self._last_delivery_useful_at=self.clock()
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
        if reply_id is not None and self.journal is not False:
            now=utc_iso(self.now())
            scope=Scope(str(message.guild.id),str(message.channel.id))
            text='[Extrait vocal : '+sample.id+']' if format_used=='voice' else decision['reply']
            envelope=MessageEnvelope(scope,str(reply_id),str(getattr(getattr(self.bot,'user',None),'id','bot')),
                'Haddock',safe_text(text,1800),now,received_at=now,reply_to_message_id=str(message.id),
                source_url='https://discord.com/channels/'+scope.guild_id+'/'+scope.channel_id+'/'+str(reply_id),is_bot=True)
            saved=self._persist(envelope)
            if saved is not None:
                self.coordinator.observe(saved)
        self.cadence.sent(direct,message.author.id,conversation)
        self.bot.write_status(last_luna_reply={'sent_utc':stamp(),'reply_id':str(reply_id),
            'mode':'direct' if direct else 'ambient','format':format_used},last_luna_delivery_error=None)

        return reply_id

    async def close(self):
        self.closed=True
        self._wake.set()
        if self.task and not self.task.done():
            self.task.cancel()
            await asyncio.gather(self.task,return_exceptions=True)
        if self._overflow_task and not self._overflow_task.done():
            self._overflow_task.cancel()
            await asyncio.gather(self._overflow_task,return_exceptions=True)
        if self.media is not None:
            await self.media.close()
        self.memory.rows.clear()
        self.memory.bot_ids.clear()
