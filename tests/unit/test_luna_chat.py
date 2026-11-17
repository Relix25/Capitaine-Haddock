import asyncio
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import time
import unittest
import discord
from unittest.mock import AsyncMock, MagicMock, patch

from courswatch.discord_app.bot import CourseBot
from courswatch.conversation.brain import BUDGET_REPLY, BudgetExhausted, ChatMemory, ChatCadence, LunaError, OFFLINE_REPLY, REFUSAL_REPLY
from courswatch.conversation.chat import LunaChat
from courswatch.conversation.metrics import MeasuredResult


class ChatTests(unittest.IsolatedAsyncioTestCase):
    async def test_social_retrieval_is_versioned_and_deletion_invalidates_reply(self):
        from courswatch.memory import Journal
        from courswatch.knowledge import KnowledgeService
        from courswatch.conversation.contracts import MessageEnvelope, Scope
        fixed=datetime(2026,11,17,12,tzinfo=timezone.utc)
        chat=self.chat();self.fast_clock(chat)
        chat.journal=Journal(chat.data/'conversation.db',now=lambda:fixed)
        chat.knowledge=KnowledgeService(chat.data,{},journal=chat.journal,clock=lambda:fixed)
        scope=Scope('1','2')
        old=MessageEnvelope(scope,'10','4','Alice','Je préfère le bleu.',(fixed-timedelta(days=7)).isoformat())
        target=MessageEnvelope(scope,'20','5','Bob','Haddock, pourquoi Alice préfère le bleu ?',fixed.isoformat())
        chat.journal.ingest(old);chat.journal.ingest(target)
        chat.coordinator.observe(target)
        turn=chat.coordinator.enqueue(target,True)
        snapshot=chat.coordinator.freeze(turn)
        facts=await chat.facts(snapshot)
        self.assertIn('bleu',facts)
        bundle=chat._evidence[snapshot.turn_id]
        self.assertTrue(any(proof.source_id=='10' for proof in bundle.evidence))
        chat.journal.delete(scope,'10')
        self.assertFalse(await chat._valid(snapshot,self.message(id=20),bundle))
        await chat.close()

    async def test_command_help_is_local_and_does_not_use_historical_model(self):
        chat=self.chat();self.fast_clock(chat)
        chat.bot.conversation_command_help=lambda text:'/modele a été retirée. Utilise /settings.'
        message=self.message(content='Haddock, comment utiliser /modele ?')
        chat.accept(message,False);await chat.task
        self.assertIn('/settings',self.spoken_reply(message))
        chat.brain.decide.assert_not_called()
        await chat.close()

    async def test_inflight_provision_is_not_reported_as_exhausted_until_midnight(self):
        chat=self.chat();self.fast_clock(chat)
        chat.brain.budget=SimpleNamespace(snapshot=lambda:{'blocked':False,'remaining_tokens':0,'reserved_tokens':100})
        chat.brain.decide.side_effect=BudgetExhausted()
        message=self.message()
        chat.accept(message,False)
        await chat.task
        reply=self.spoken_reply(message)
        self.assertIn('appels en cours',reply)
        self.assertNotIn('minuit',reply)
        await chat.close()

    def message(self,**changes):
        data = dict(id=123,guild=SimpleNamespace(id=1),channel=SimpleNamespace(id=2,send=AsyncMock()),
            author=SimpleNamespace(id=4,bot=False,display_name='Camarade',roles=[SimpleNamespace(id=3)]),
            content='Haddock, tu vas bien ?',mentions=[],webhook_id=None,reference=None,
            reply=AsyncMock(return_value=SimpleNamespace(id=456)))
        data.update(changes)
        return SimpleNamespace(**data)

    def chat(self):
        bot = SimpleNamespace(user=SimpleNamespace(id=99),voice_samples=[],voice_probability=.5,
            token_value='fake',last_voice_sample=None,write_status=MagicMock())
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        brain = SimpleNamespace(decide=MagicMock(return_value={'action':'text','reply':'À bord !','sample_id':'none'}))
        chat = LunaChat(bot,Path(directory.name),{'provider':'commandcode','model':'deepseek/deepseek-v4.1-flash'},
                        lambda:{},journal=False,knowledge=False,brain=brain)
        return chat

    def spoken_reply(self,message):
        return message.reply.await_args.args[0].split('\n\n-# ',1)[0]

    def fast_clock(self,chat,on_sleep=None):
        clock,delays = [100.0],[]
        async def sleep(delay):
            delays.append(delay)
            if on_sleep is not None:
                on_sleep(delay)
            clock[0] += delay
        chat.clock = lambda:clock[0]
        chat.sleep = sleep
        chat.memory.clock = chat.cadence.clock = chat.clock
        return clock,delays

    def typing_context(self,message):
        state = SimpleNamespace(active=False)
        async def enter():
            state.active = True
        async def leave(*args):
            state.active = False
        manager = MagicMock()
        manager.__aenter__ = AsyncMock(side_effect=enter)
        manager.__aexit__ = AsyncMock(side_effect=leave)
        message.channel.typing = MagicMock(return_value=manager)
        return manager,state

    async def test_direct_reply_and_coalescing_preserve_context_without_ping(self):
        chat = self.chat()
        message = self.message()
        chat.accept(message,False)
        next_message = self.message(id=124,content='Une précision : le projet AAIT.',reference=SimpleNamespace(message_id=123,resolved=None))
        chat.accept(next_message,False)
        await chat.task
        message.reply.assert_not_awaited()
        next_message.reply.assert_awaited_once()
        self.assertEqual(chat.brain.decide.call_count,1)
        self.assertEqual(len(chat.brain.decide.call_args.args[0]),2)
        self.assertFalse(next_message.reply.await_args.kwargs['mention_author'])
        self.assertFalse(next_message.reply.await_args.kwargs['allowed_mentions'].everyone)
        self.assertTrue(chat.cadence.followup(4))
        await chat.close()

    async def test_message_during_short_direct_debounce_is_still_coalesced(self):
        chat = self.chat()
        first = self.message()
        second = self.message(id=124,content='Haddock, une seconde précision.')
        inserted = []
        def interleave(delay):
            if not inserted:
                inserted.append(True)
                chat.accept(second,False)
        _,delays = self.fast_clock(chat,interleave)
        chat.accept(first,False)
        await chat.task
        self.assertEqual(delays,[0.15])
        first.reply.assert_not_awaited()
        second.reply.assert_awaited_once()
        chat.brain.decide.assert_called_once()
        self.assertEqual(len(chat.brain.decide.call_args.args[0]),2)
        await chat.close()

    async def test_chat_timings_separate_queue_model_and_delivery_without_private_data(self):
        chat = self.chat()
        clock,delays = self.fast_clock(chat)
        private = 'SENTINEL_PRIVATE_CONVERSATION_NEVER_PERSIST'
        message = self.message(id=123456789012345678,content='Haddock, '+private,
            created_at=datetime.now(timezone.utc)-timedelta(milliseconds=50),
            author=SimpleNamespace(id=987654321012345678,display_name='PRIVATE_SPEAKER'))
        def decide(*args,**kwargs):
            clock[0] += 0.25
            return {'action':'text','reply':private,'sample_id':'none'}
        async def reply(*args,**kwargs):
            clock[0] += 0.05
            return SimpleNamespace(id=456)
        chat.brain.decide.side_effect = decide
        message.reply.side_effect = reply
        chat.accept(message,False)
        await chat.task
        timing = chat.bot.write_status.call_args.kwargs['last_chat_timing']
        self.assertEqual(delays,[0.15])
        self.assertEqual({key:timing[key] for key in ('queue_ms','model_ms','delivery_ms','total_ms')},
                         {'queue_ms':150.0,'model_ms':250.0,'delivery_ms':50.0,'total_ms':450.0})
        self.assertGreaterEqual(timing['gateway_ms'],0)
        serialized = json.dumps(timing)
        for value in (private,'PRIVATE_SPEAKER','123456789012345678','987654321012345678'):
            self.assertNotIn(value,serialized)
        self.assertEqual(timing['first_useful_ms'],450.0)
        self.assertEqual(timing['retrieval_ms'],0.0)
        await chat.close()

    async def test_typing_spans_direct_facts_and_model_but_stops_before_reply(self):
        chat = self.chat()
        self.fast_clock(chat)
        message = self.message()
        manager,state = self.typing_context(message)
        async def facts(snapshot,profile=None):
            self.assertTrue(state.active)
            return 'synthetic facts'
        def decide(*args,**kwargs):
            self.assertTrue(state.active)
            return {'action':'text','reply':'Réponse synthétique.','sample_id':'none'}
        async def reply(*args,**kwargs):
            self.assertFalse(state.active)
            return SimpleNamespace(id=456)
        chat.facts = facts
        chat.brain.decide.side_effect = decide
        message.reply.side_effect = reply
        chat.accept(message,False)
        await chat.task
        manager.__aenter__.assert_awaited_once()
        manager.__aexit__.assert_awaited_once()
        chat.brain.decide.assert_called_once()
        message.reply.assert_awaited_once()
        await chat.close()

    async def test_ambient_keeps_four_second_debounce_and_never_types(self):
        chat = self.chat()
        _,delays = self.fast_clock(chat)
        chat.brain.decide.return_value = {'action':'ignore','reply':'','sample_id':'none'}
        message = self.message(content='Le cours commence à neuf heures demain.')
        message.channel.typing = MagicMock(side_effect=AssertionError('ambient typing'))
        chat.accept(message,False)
        await chat.task
        self.assertEqual(delays,[4])
        message.channel.typing.assert_not_called()
        message.reply.assert_not_awaited()
        await chat.close()

    async def test_typing_is_cleaned_up_before_a_model_error_fallback(self):
        chat = self.chat()
        self.fast_clock(chat)
        message = self.message()
        manager,state = self.typing_context(message)
        chat.brain.decide.side_effect = LunaError('http_429')
        chat.accept(message,False)
        await chat.task
        manager.__aexit__.assert_awaited_once()
        self.assertIs(manager.__aexit__.await_args.args[0],LunaError)
        self.assertFalse(state.active)
        self.assertEqual(self.spoken_reply(message),OFFLINE_REPLY)
        chat.brain.decide.assert_called_once()
        await chat.close()

    async def test_unavailable_typing_does_not_prevent_a_direct_reply(self):
        chat = self.chat()
        self.fast_clock(chat)
        message = self.message()
        manager,_ = self.typing_context(message)
        manager.__aenter__.side_effect = OSError('private network detail')
        chat.accept(message,False)
        await chat.task
        chat.brain.decide.assert_called_once()
        message.reply.assert_awaited_once()
        manager.__aexit__.assert_not_awaited()
        self.assertNotIn('private network detail',str(chat.bot.write_status.call_args_list))
        await chat.close()

    async def test_close_cleans_up_active_typing_without_calling_the_model(self):
        chat = self.chat()
        self.fast_clock(chat)
        message = self.message()
        manager,state = self.typing_context(message)
        facts_started = asyncio.Event()
        async def facts(snapshot,profile=None):
            facts_started.set()
            await asyncio.Event().wait()
        chat.facts = facts
        chat.accept(message,False)
        await asyncio.wait_for(facts_started.wait(),timeout=1)
        await chat.close()
        manager.__aexit__.assert_awaited_once()
        self.assertIs(manager.__aexit__.await_args.args[0],asyncio.CancelledError)
        self.assertFalse(state.active)
        chat.brain.decide.assert_not_called()
        message.reply.assert_not_awaited()

    async def test_quota_returns_static_phrase_and_commands_still_exist(self):
        chat = self.chat()
        chat.brain.decide.side_effect = BudgetExhausted()
        message = self.message()
        chat.accept(message,False)
        await chat.task
        self.assertEqual(self.spoken_reply(message),BUDGET_REPLY)
        self.assertFalse(chat.cadence.followup(4))
        await chat.close()

    async def test_uninteresting_ambient_calls_nothing_and_ignores_boring_decision(self):
        chat = self.chat()
        chat.accept(self.message(content='ok'),False)
        self.assertIsNone(chat.task)
        chat.brain.decide.return_value = {'action':'ignore','reply':'','sample_id':'none'}
        message = self.message(content='Le cours commence à neuf heures demain.')
        chat.accept(message,False)
        await chat.task
        message.reply.assert_not_awaited()
        self.assertFalse(chat.brain.decide.call_args.args[1])
        self.assertFalse(chat.cadence.ambient_allowed())
        await chat.close()

    async def test_scope_filters_precede_any_ai_context_collection(self):
        config = {'discord':{'guild_id':'1','channel_id':'2','role_ids':['3'],
            'application_id':'99','luna':{'enabled':True,'provider':'commandcode','model':'deepseek/deepseek-v4.1-flash'}}}
        with tempfile.TemporaryDirectory() as directory,patch('courswatch.discord_app.bot.configuration',return_value=config), \
             patch('courswatch.discord_app.bot.DATA',Path(directory)),patch('courswatch.conversation.brain.read_api_key',return_value='fake'):
            bot = CourseBot('fake')
            bot.luna.accept = MagicMock()
            for message in [self.message(guild=None),self.message(channel=SimpleNamespace(id=999)),
                self.message(webhook_id=123),self.message(author=SimpleNamespace(bot=True)),
                self.message(author=SimpleNamespace(bot=False,roles=[]))]:
                await bot.on_message(message)
            bot.luna.accept.assert_not_called()
            await bot.on_message(self.message())
            bot.luna.accept.assert_called_once()
            self.assertEqual({c.name for c in bot.tree.get_commands(guild=bot.scope)},
                             {'a-faire','cours','actualiser','status','settings'})
            await bot.close()

    async def test_meaningful_text_is_not_randomly_replaced_with_audio(self):
        chat = self.chat()
        chat.bot.voice_samples = [SimpleNamespace(id='cafe')]
        message = self.message()
        with patch('courswatch.conversation.chat.send_voice_message') as send,patch('courswatch.conversation.chat.random.random',return_value=.1):
            await chat.deliver(message,{'action':'text','reply':'Voici le plan.','sample_id':'none'},True)
            send.assert_not_called()
        message.reply.assert_awaited_once()
        await chat.close()

    async def test_api_failure_opens_short_circuit_instead_of_retrying_every_message(self):
        chat = self.chat()
        chat.brain.decide.side_effect = LunaError('http_429')
        chat.accept(self.message(),False)
        await chat.task
        chat.accept(self.message(id=124),False)
        await chat.task
        self.assertEqual(chat.brain.decide.call_count,1)
        await chat.close()

    async def test_bad_model_reply_does_not_block_the_next_message_for_a_minute(self):
        chat = self.chat()
        chat.brain.decide.side_effect = [LunaError('invalid_json'),{'action':'text','reply':'Je t’écoute, forban.','sample_id':'none'}]
        first,second = self.message(),self.message(id=124)
        chat.accept(first,False)
        await chat.task
        self.assertNotEqual(self.spoken_reply(first),OFFLINE_REPLY)
        self.assertEqual(chat.retry_after,0)
        chat.accept(second,False)
        await chat.task
        self.assertEqual(chat.brain.decide.call_count,2)
        self.assertEqual(self.spoken_reply(second),'Je t’écoute, forban.')
        await chat.close()

    async def test_refusal_is_local_to_that_request_and_is_not_retried(self):
        chat = self.chat()
        chat.brain.decide.side_effect = LunaError('model_refusal')
        message = self.message()
        chat.accept(message,False)
        await chat.task
        self.assertEqual(self.spoken_reply(message),REFUSAL_REPLY)
        self.assertEqual(chat.brain.decide.call_count,1)
        self.assertEqual(chat.retry_after,0)
        await chat.close()

    async def test_contextual_sample_can_be_sent_and_audio_failure_returns_model_text(self):
        from courswatch.conversation.voice import VoiceMessageError
        chat = self.chat()
        chat.bot.voice_samples = [SimpleNamespace(id='cafe')]
        message = self.message()
        choice = {'action':'sample','reply':'Ce café a décidé de prendre le large !','sample_id':'cafe'}
        with patch('courswatch.conversation.chat.random.random',return_value=.1), \
             patch('courswatch.conversation.chat.send_voice_message',return_value={'id':'987'}) as send:
            await chat.deliver(message,choice,True)
            send.assert_called_once()
            message.reply.assert_not_awaited()
            message.channel.send.assert_awaited_once()
            self.assertEqual(message.channel.send.await_args.kwargs['reference'].message_id,987)
            self.assertFalse(message.channel.send.await_args.kwargs['allowed_mentions'].roles)
        chat.bot.last_voice_sample = None
        with patch('courswatch.conversation.chat.random.random',return_value=.1), \
             patch('courswatch.conversation.chat.send_voice_message',side_effect=VoiceMessageError('Discord voice HTTP 400')):
            await chat.deliver(message,choice,True)
            self.assertEqual(self.spoken_reply(message),choice['reply'])
        await chat.close()

    async def test_metrics_are_shown_but_never_return_to_model_memory(self):
        chat=self.chat();message=self.message()
        decision=MeasuredResult({'action':'text','reply':'Paroles du capitaine.','sample_id':'none'},
            {'model':'deepseek/deepseek-v4.1-flash','ttft_ms':200,'api_ms':700,'total_tokens':121,
             'input_tokens':100,'output_tokens':21,'cached_input_tokens':80,'output_tps':40})
        await chat.deliver(message,decision,True)
        content=message.reply.await_args.args[0]
        self.assertIn('\n\n-# DeepSeek V4.1 Flash',content)
        self.assertIn('TTFT 0,20 s',content)
        self.assertIn('Total IA 0,70 s',content)
        self.assertIn('≈ 40,0 tok/s',content)
        self.assertIn('121 tokens',content)
        self.assertEqual(chat.memory.context()[-1]['text'],'Paroles du capitaine.')
        self.assertNotIn('TTFT',str(chat.memory.context()))
        self.assertFalse(message.reply.await_args.kwargs['allowed_mentions'].roles)
        await chat.close()

    async def test_voice_footer_failure_does_not_duplicate_an_already_delivered_audio(self):
        chat=self.chat();message=self.message()
        chat.bot.voice_samples=[SimpleNamespace(id='cafe')]
        message.channel.send.side_effect=discord.HTTPException(SimpleNamespace(status=500,reason='synthetic'),
                                                               {'message':'PRIVATE_SENTINEL','code':0})
        with patch('courswatch.conversation.chat.random.random',return_value=.1), \
             patch('courswatch.conversation.chat.send_voice_message',return_value={'id':'987'}):
            await chat.deliver(message,{'action':'sample','reply':'Un café récalcitrant.','sample_id':'cafe'},True)
        message.reply.assert_not_awaited()
        self.assertIn(987,chat.memory.bot_ids)
        self.assertNotIn('PRIVATE_SENTINEL',str(chat.bot.write_status.call_args_list))
        self.assertEqual(chat.bot.write_status.call_args.kwargs['last_luna_reply']['format'],'voice')
        await chat.close()

    async def test_debug_off_hides_text_footer_and_keeps_spoken_memory(self):
        chat=self.chat();chat.debug_enabled=False;message=self.message()
        decision=MeasuredResult({'action':'text','reply':'À bord !','sample_id':'none'},
            {'model':'tencent/hy4-flash','ttft_ms':100,'api_ms':300,'total_tokens':50})
        await chat.deliver(message,decision,True)
        self.assertEqual(message.reply.await_args.args[0],'À bord !')
        self.assertEqual(chat.memory.context()[-1]['text'],'À bord !')
        self.assertEqual(decision.metrics['total_tokens'],50)
        await chat.close()

    async def test_debug_off_sends_voice_without_companion_metrics_message(self):
        chat=self.chat();chat.debug_enabled=False;message=self.message()
        chat.bot.voice_samples=[SimpleNamespace(id='cafe')]
        with patch('courswatch.conversation.chat.random.random',return_value=.1), \
             patch('courswatch.conversation.chat.send_voice_message',return_value={'id':'987'}):
            await chat.deliver(message,{'action':'sample','reply':'Un café.','sample_id':'cafe'},True)
        message.reply.assert_not_awaited()
        message.channel.send.assert_not_awaited()
        self.assertIn(987,chat.memory.bot_ids)
        await chat.close()


    async def test_two_authors_keep_independent_requests_and_attributed_snapshots(self):
        chat=self.chat();self.fast_clock(chat)
        alice=self.message(content='Haddock, date de AAIT ?')
        bob=self.message(id=124,content='Haddock, explique BDFA.',
            author=SimpleNamespace(id=5,display_name='Camarade'))
        chat.accept(alice,False);chat.accept(bob,False)
        await chat.task
        alice.reply.assert_awaited_once();bob.reply.assert_awaited_once()
        self.assertEqual(chat.brain.decide.call_count,2)
        first,second=chat.brain.decide.call_args_list
        self.assertEqual(first.kwargs['request'].requester_ids,('4',))
        self.assertEqual(second.kwargs['request'].requester_ids,('5',))
        self.assertNotIn('BDFA',str(first.args[0]))
        self.assertNotIn('AAIT',str(second.args[0]))
        await chat.close()

    async def test_same_author_independent_topics_are_not_overwritten(self):
        chat=self.chat();self.fast_clock(chat)
        first=self.message(content='Haddock, date AAIT ?')
        second=self.message(id=124,content='Haddock, où est BDFA ?')
        chat.accept(first,False);chat.accept(second,False)
        await chat.task
        first.reply.assert_awaited_once();second.reply.assert_awaited_once()
        self.assertEqual(chat.brain.decide.call_count,2)
        await chat.close()

    async def test_facts_read_snapshot_before_other_author_arrives(self):
        chat=self.chat();self.fast_clock(chat)
        entered=asyncio.Event();release=asyncio.Event()
        goals=[]
        async def facts(snapshot,profile=None):
            goals.append(snapshot.goal)
            if len(goals)==1:
                entered.set();await release.wait()
            return ''
        chat.facts=facts
        alice=self.message(content='Haddock, date AAIT ?')
        bob=self.message(id=124,content='Haddock, où est BDFA ?',author=SimpleNamespace(id=5,display_name='Bob'))
        chat.accept(alice,False)
        await asyncio.wait_for(entered.wait(),1)
        chat.accept(bob,False);release.set()
        await chat.task
        self.assertEqual(goals,['Haddock, date AAIT ?','Haddock, où est BDFA ?'])
        self.assertEqual(chat.brain.decide.call_args_list[0].kwargs['request'].reply_target_id,'123')
        alice.reply.assert_awaited_once();bob.reply.assert_awaited_once()
        await chat.close()

    async def test_relevant_edit_during_generation_retries_once_before_publication(self):
        from dataclasses import replace
        from courswatch.conversation.ingestion import envelope_from_message
        import threading
        chat=self.chat();self.fast_clock(chat)
        started=asyncio.Event();release=threading.Event();loop=asyncio.get_running_loop()
        calls=[]
        def decide(context,*args,**kwargs):
            calls.append(context)
            if len(calls)==1:
                loop.call_soon_threadsafe(started.set)
                release.wait(timeout=2)
            return {'action':'text','reply':context[-1]['text'],'sample_id':'none'}
        chat.brain.decide.side_effect=decide
        message=self.message(content='Haddock, parle du premier sujet.')
        chat.accept(message,False)
        await asyncio.wait_for(started.wait(),1)
        edited=replace(envelope_from_message(message),text='Haddock, parle du sujet corrigé.',revision=2,
                       edited_at=datetime.now(timezone.utc).isoformat())
        chat.edited(edited);release.set()
        await chat.task
        self.assertEqual(len(calls),2)
        message.reply.assert_awaited_once()
        self.assertIn('corrigé',self.spoken_reply(message))
        await chat.close()

    async def test_human_answer_during_generation_prevents_late_reply(self):
        import threading
        chat=self.chat();self.fast_clock(chat)
        started=asyncio.Event();release=threading.Event();loop=asyncio.get_running_loop()
        def decide(*args,**kwargs):
            loop.call_soon_threadsafe(started.set);release.wait(timeout=2)
            return {'action':'text','reply':'Réponse devenue inutile.','sample_id':'none'}
        chat.brain.decide.side_effect=decide
        message=self.message();chat.accept(message,False)
        await asyncio.wait_for(started.wait(),1)
        human=self.message(id=124,content='Voici la réponse complète.',author=SimpleNamespace(id=5,display_name='Bob'),
            reference=SimpleNamespace(message_id=123,resolved=None))
        chat.accept(human,False);release.set()
        await chat.task
        message.reply.assert_not_awaited();human.reply.assert_not_awaited()
        self.assertEqual(chat.brain.decide.call_count,1)
        await chat.close()

    async def test_deletion_during_generation_prevents_publication(self):
        from courswatch.conversation.contracts import Scope
        import threading
        chat=self.chat();self.fast_clock(chat)
        started=asyncio.Event();release=threading.Event();loop=asyncio.get_running_loop()
        def decide(*args,**kwargs):
            loop.call_soon_threadsafe(started.set);release.wait(timeout=2)
            return {'action':'text','reply':'Source supprimée.','sample_id':'none'}
        chat.brain.decide.side_effect=decide
        message=self.message();chat.accept(message,False)
        await asyncio.wait_for(started.wait(),1)
        chat.deleted(Scope('1','2'),'123');release.set()
        await chat.task
        message.reply.assert_not_awaited()
        self.assertEqual(chat.brain.decide.call_count,1)
        await chat.close()

    async def test_async_authorization_cannot_allow_a_correction_received_during_check(self):
        from courswatch.conversation.contracts import Scope
        chat=self.chat();self.fast_clock(chat)
        async def allowed(message):
            chat.deleted(Scope('1','2'),str(message.id))
            return True
        chat.bot.conversation_publish_allowed=allowed
        message=self.message();chat.accept(message,False)
        await chat.task
        message.reply.assert_not_awaited()
        await chat.close()

    async def test_wake_event_interrupts_ambient_wait_without_waiting_for_timer(self):
        chat=self.chat()
        waiting=asyncio.create_task(chat._wait(1000))
        await asyncio.sleep(0)
        chat._wake.set()
        await asyncio.wait_for(waiting,1)
        await chat.close()

    async def test_backfill_and_gratitude_are_observed_without_paid_call(self):
        from dataclasses import replace
        from courswatch.conversation.ingestion import envelope_from_message
        chat=self.chat()
        message=self.message();envelope=replace(envelope_from_message(message),ingestion_mode='backfill')
        decision=chat.observe(envelope,message,True)
        self.assertEqual(decision.reason_code,'historical_ingestion')
        thanks=self.message(id=124,content='Merci Haddock !')
        decision=chat.accept(thanks,False)
        self.assertEqual(decision.reason_code,'acknowledgement')
        self.assertIsNone(chat.task)
        chat.brain.decide.assert_not_called()
        await chat.close()

    async def test_image_unavailable_does_not_pretend_text_model_saw_it(self):
        from dataclasses import replace
        from courswatch.conversation.contracts import MediaDescriptor
        from courswatch.conversation.ingestion import envelope_from_message
        chat=self.chat();self.fast_clock(chat)
        message=self.message(content='Haddock, que montre cette image ?')
        envelope=replace(envelope_from_message(message),media=(MediaDescriptor('img','image','https://cdn.discordapp.com/attachments/1/2/f.png'),))
        chat.observe(envelope,message,True)
        await chat.task
        self.assertIn('capacités actuellement disponibles',self.spoken_reply(message))
        chat.brain.decide.assert_not_called()
        await chat.close()

    async def test_source_clock_expires_live_requests_without_affecting_memory(self):
        from dataclasses import replace
        from courswatch.conversation.ingestion import envelope_from_message
        chat=self.chat()
        fixed=datetime(2026,11,17,tzinfo=timezone.utc);chat.now=lambda:fixed
        message=self.message()
        envelope=replace(envelope_from_message(message,now=fixed),created_at=(fixed-timedelta(days=7)).isoformat())
        decision=chat.observe(envelope,message,True)
        self.assertEqual(decision.reason_code,'expired_request')
        self.assertIsNone(chat.task)
        self.assertEqual(chat.memory.context()[-1]['text'],message.content)
        await chat.close()


    async def test_uncertain_voice_upload_never_falls_back_to_duplicate_text(self):
        from courswatch.conversation.voice import VoiceMessageError
        chat=self.chat();message=self.message()
        chat.bot.voice_samples=[SimpleNamespace(id='cafe')]
        with patch('courswatch.conversation.chat.random.random',return_value=.1), \
             patch('courswatch.conversation.chat.send_voice_message',side_effect=VoiceMessageError('Discord voice unavailable')):
            receipt=await chat.deliver(message,{'action':'sample','reply':'Paroles de secours.','sample_id':'cafe'},True)
        self.assertIsNone(receipt)
        message.reply.assert_not_awaited()
        self.assertEqual(chat.bot.write_status.call_args.kwargs['last_voice_error']['outcome'],'uncertain')
        await chat.close()

    async def test_journal_survives_restart_without_dedupe_collision_or_footer_history(self):
        from courswatch.memory import Journal
        from courswatch.conversation.contracts import Scope
        directory=tempfile.TemporaryDirectory();self.addCleanup(directory.cleanup)
        journal=Journal(Path(directory.name)/'conversation.db')
        first_chat=self.chat();first_chat.journal=journal;self.fast_clock(first_chat)
        first=self.message();first_chat.accept(first,False);await first_chat.task
        stored=journal.active(Scope('1','2'),'456')
        self.assertTrue(stored.is_bot)
        self.assertEqual(stored.reply_to_message_id,'123')
        self.assertEqual(stored.text,'À bord !')
        self.assertNotIn('TTFT',stored.text)
        await first_chat.close()
        second_chat=self.chat();second_chat.journal=journal;self.fast_clock(second_chat)
        second=self.message(id=124,reply=AsyncMock(return_value=SimpleNamespace(id=457)))
        second_chat.accept(second,False);await second_chat.task
        second.reply.assert_awaited_once()
        self.assertEqual(second_chat.brain.decide.call_count,1)
        self.assertTrue(journal.active(Scope('1','2'),'457').is_bot)
        second_chat.accept(second,False)
        self.assertEqual(second_chat.brain.decide.call_count,1)
        await second_chat.close()

    async def test_missing_course_proof_produces_local_explicit_absence(self):
        from courswatch.conversation.contracts import EvidenceBundle
        chat=self.chat();self.fast_clock(chat)
        chat.knowledge=SimpleNamespace(query=MagicMock(return_value=EvidenceBundle(coverage='Aucune source probante.')),
            revalidate=MagicMock(return_value=True))
        message=self.message(content='Haddock, quelle date pour le projet AAIT ?')
        chat.accept(message,False);await chat.task
        self.assertEqual(self.spoken_reply(message),'Aucune source probante.')
        chat.brain.decide.assert_not_called()
        await chat.close()

    async def test_source_revoked_during_last_evidence_check_prevents_send(self):
        from courswatch.conversation.contracts import EvidenceBundle, Evidence, Scope
        chat=self.chat();self.fast_clock(chat)
        evidence=Evidence('proof','source',Scope('1','2'),'official','https://example.invalid/course','AAIT',
            'Source synthétique.','v1',datetime.now(timezone.utc).isoformat())
        chat.knowledge=SimpleNamespace(query=MagicMock(return_value=EvidenceBundle(evidence=(evidence,))),
            revalidate=MagicMock(return_value=True))
        chat.bot.conversation_evidence_allowed=AsyncMock(return_value=False)
        message=self.message(content='Haddock, la date de AAIT ?')
        chat.accept(message,False);await chat.task
        message.reply.assert_not_awaited()
        chat.brain.decide.assert_not_called()
        self.assertEqual(chat.knowledge.query.call_count,2)
        await chat.close()

    async def test_direct_queue_overflow_is_explicit_without_model_or_overwrite(self):
        chat=self.chat();self.fast_clock(chat)
        chat.coordinator.max_pending=1
        first=self.message(content='Haddock, explique AAIT.')
        second=self.message(id=124,content='Haddock, explique BDFA.')
        chat.accept(first,False)
        decision=chat.accept(second,False)
        self.assertEqual(decision.reason_code,'queue_full')
        await chat.task
        await chat._overflow_task
        first.reply.assert_awaited_once();second.reply.assert_awaited_once()
        self.assertIn('demandes',self.spoken_reply(second))
        self.assertEqual(chat.brain.decide.call_count,1)
        await chat.close()

    async def test_evidence_revoked_during_last_publish_await_is_checked_synchronously(self):
        from courswatch.conversation.contracts import EvidenceBundle, Evidence, Scope
        chat=self.chat();self.fast_clock(chat)
        permitted=[True]
        evidence=Evidence('proof','source',Scope('1','2'),'official','https://example.invalid/course','AAIT',
            'Source synthétique.','v1',datetime.now(timezone.utc).isoformat())
        chat.knowledge=SimpleNamespace(query=MagicMock(return_value=EvidenceBundle(evidence=(evidence,))),
            revalidate=MagicMock(side_effect=lambda *args:permitted[0]))
        async def publish_allowed(message):
            permitted[0]=False
            return True
        chat.bot.conversation_publish_allowed=publish_allowed
        message=self.message(content='Haddock, la date de AAIT ?')
        chat.accept(message,False);await chat.task
        message.reply.assert_not_awaited()
        self.assertGreaterEqual(chat.knowledge.revalidate.call_count,3)
        self.assertEqual(chat.brain.decide.call_count,0)
        await chat.close()

    async def test_three_fragments_of_incomplete_request_coalesce_with_bounded_wait(self):
        chat=self.chat();self.fast_clock(chat)
        first=self.message(content='Haddock, je voulais savoir…')
        second=self.message(id=124,content='Le projet AAIT')
        third=self.message(id=125,content='La date ?')
        chat.accept(first,False);chat.accept(second,False);chat.accept(third,False)
        await chat.task
        first.reply.assert_not_awaited();second.reply.assert_not_awaited();third.reply.assert_awaited_once()
        self.assertEqual(chat.brain.decide.call_count,1)
        snapshot=chat.brain.decide.call_args.kwargs['request']
        self.assertEqual(snapshot.trigger_message_ids,('123','124','125'))
        self.assertEqual(snapshot.reply_target_id,'125')
        await chat.close()

    async def test_old_bot_reply_loaded_from_durable_journal_beyond_warm_window(self):
        from courswatch.memory import Journal
        from courswatch.conversation.contracts import MessageEnvelope, Scope
        fixed=datetime(2026,11,17,12,tzinfo=timezone.utc)
        directory=tempfile.TemporaryDirectory();self.addCleanup(directory.cleanup)
        journal=Journal(Path(directory.name)/'conversation.db',now=lambda:fixed)
        scope=Scope('1','2')
        journal.ingest(MessageEnvelope(scope,'42','99','Haddock','Une explication ancienne.',
            (fixed-timedelta(days=7)).isoformat(),is_bot=True))
        for index in range(31):
            journal.ingest(MessageEnvelope(scope,str(index+100),'4','Camarade','Message humain récent.',
                (fixed-timedelta(minutes=31-index)).isoformat()))
        self.assertNotIn('42',[row.message_id for row in journal.recent(scope,30)])
        chat=self.chat();chat.journal=journal;chat.now=lambda:fixed;self.fast_clock(chat)
        chat.settings['participation_mode']='direct_only'
        message=self.message(id=999,content='Tu peux préciser ?',
            reference=SimpleNamespace(message_id=42,channel_id=2,guild_id=1,
                resolved=SimpleNamespace(author=SimpleNamespace(id=99))))
        chat.accept(message,False);await chat.task
        message.reply.assert_awaited_once()
        snapshot=chat.brain.decide.call_args.kwargs['request']
        self.assertEqual(snapshot.reply_target_id,'999')
        self.assertEqual([row.message_id for row in snapshot.messages],['42','999'])
        self.assertTrue(snapshot.messages[0].is_bot)
        await chat.close()

    async def test_prior_media_observation_keeps_date_author_limitations_and_readable_ocr(self):
        from dataclasses import replace
        from courswatch.conversation.contracts import MediaDescriptor
        from courswatch.conversation.ingestion import envelope_from_message
        chat=self.chat();self.fast_clock(chat)
        message=self.message(content='Haddock, décris cette image.')
        envelope=replace(envelope_from_message(message),media=(MediaDescriptor('img','image','https://cdn.discordapp.com/attachments/1/2/f.png'),))
        observation={'status':'ready','message_id':'123','media_id':'img','description':'Un panneau bleu.',
            'analysed_at':'2026-11-10T10:00:00+00:00','prior_observation':True,
            'cache_hit':True,'metrics':{'media_fetch_ms':0,'media_decode_ms':0,'vision_ms':0},
            'ocr':[{'text':'TEST-17','at_ms':1200,'quality':'uncertain'}],
            'animation':'Le panneau apparaît à la fin.','limitations':['Trois images échantillonnées.']}
        chat.media=SimpleNamespace(submit=MagicMock(),enrich=AsyncMock(return_value=(observation,)),close=AsyncMock())
        chat.observe(envelope,message,True);await chat.task
        text=self.spoken_reply(message)
        for value in ('Observation antérieure','média non revérifié','Camarade','10/11/2026','TEST-17','incertain à 1,20 s','Trois images échantillonnées.'):
            self.assertIn(value,text)
        self.assertNotIn("{'text':",text)
        timing=chat.bot.write_status.call_args.kwargs['last_chat_timing']
        self.assertEqual({name:timing[name] for name in ('media_fetch_ms','media_decode_ms','vision_ms','media_cache_hits','media_ready')},
                         {'media_fetch_ms':0,'media_decode_ms':0,'vision_ms':0,'media_cache_hits':1,'media_ready':1})
        self.assertNotIn('panneau',json.dumps(timing))
        chat.brain.decide.assert_not_called()
        await chat.close()

    async def test_large_media_description_is_not_cut_mid_observation_or_citation(self):
        from dataclasses import replace
        from courswatch.conversation.contracts import MediaDescriptor
        from courswatch.conversation.ingestion import envelope_from_message
        chat=self.chat();self.fast_clock(chat)
        message=self.message(content='Haddock, décris ce GIF.')
        envelope=replace(envelope_from_message(message),media=(MediaDescriptor('img','animation','https://cdn.discordapp.com/attachments/1/2/f.gif'),))
        observation={'status':'ready','message_id':'123','media_id':'img','description':'Un long descriptif. '*160,
            'ocr':[],'animation':'Fin.','limitations':['Échantillon borné.']}
        chat.media=SimpleNamespace(submit=MagicMock(),enrich=AsyncMock(return_value=(observation,)),close=AsyncMock())
        chat.observe(envelope,message,True);await chat.task
        text=self.spoken_reply(message)
        self.assertIn('trop longue',text)
        self.assertNotIn('Un long descriptif',text)
        self.assertNotIn('[Image](',text)
        chat.brain.decide.assert_not_called()
        await chat.close()

    async def test_prior_media_view_validates_against_canonical_persisted_observation(self):
        from dataclasses import replace
        from courswatch.memory import Journal
        from courswatch.knowledge import KnowledgeService
        from courswatch.conversation.contracts import MediaDescriptor
        from courswatch.conversation.ingestion import envelope_from_message
        directory=tempfile.TemporaryDirectory();self.addCleanup(directory.cleanup)
        chat=self.chat();self.fast_clock(chat)
        journal=Journal(Path(directory.name)/'conversation.db');chat.journal=journal
        chat.knowledge=KnowledgeService(directory.name,{},journal=journal)
        message=self.message(content='Haddock, décris cette image.')
        envelope=replace(envelope_from_message(message),media=(MediaDescriptor('img','image','https://cdn.discordapp.com/attachments/1/2/f.png'),))
        canonical={'status':'ready','message_id':'123','media_id':'img','description':'Un panneau bleu.',
            'analysed_at':'2026-11-10T10:00:00+00:00','ocr':[],'animation':'','limitations':[],'cache_hit':False}
        view={**canonical,'cache_hit':True,'prior_observation':True,'metrics':{'vision_ms':0}}
        chat.media=SimpleNamespace(submit=MagicMock(),enrich=AsyncMock(return_value=(view,)),close=AsyncMock())
        chat.observe(envelope,message,True)
        journal.save_observation(envelope.scope,envelope.message_id,1,'img',canonical)
        await chat.task
        message.reply.assert_awaited_once()
        self.assertIn('Un panneau bleu.',self.spoken_reply(message))
        self.assertEqual(chat.brain.decide.call_count,0)
        await chat.close()

if __name__ == '__main__': unittest.main()
