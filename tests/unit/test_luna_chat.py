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

from course_bot import CourseBot
from luna_brain import BUDGET_REPLY, BudgetExhausted, ChatMemory, ChatCadence, LunaError, OFFLINE_REPLY, REFUSAL_REPLY
from luna_chat import LunaChat
from courswatch.conversation.metrics import MeasuredResult


class ChatTests(unittest.IsolatedAsyncioTestCase):
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
        chat = LunaChat.__new__(LunaChat)
        chat.bot,chat.data,chat.config_loader = bot,Path('/no-access'),lambda:{}
        chat.brain = SimpleNamespace(decide=MagicMock(return_value={'action':'text','reply':'À bord !','sample_id':'none'}))
        chat.model_lock = asyncio.Lock()
        chat.memory,chat.cadence = ChatMemory(),ChatCadence()
        chat.pending_direct = chat.pending_ambient = chat.task = None
        chat.closed,chat.sequence = False,0
        chat.retry_after = 0
        chat.clock,chat.sleep = time.monotonic,asyncio.sleep
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
        next_message = self.message(id=124,content='Haddock, raconte quelque chose !')
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
        def decide(*args):
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
        self.assertEqual(set(timing),{'mode','queue_ms','model_ms','delivery_ms','total_ms','gateway_ms','checked_utc'})
        await chat.close()

    async def test_typing_spans_direct_facts_and_model_but_stops_before_reply(self):
        chat = self.chat()
        self.fast_clock(chat)
        message = self.message()
        manager,state = self.typing_context(message)
        async def facts():
            self.assertTrue(state.active)
            return 'synthetic facts'
        def decide(*args):
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
        async def facts():
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
            'application_id':'99','luna':{'enabled':True}}}
        with tempfile.TemporaryDirectory() as directory,patch('course_bot.configuration',return_value=config), \
             patch('course_bot.DATA',Path(directory)),patch('luna_brain.read_api_key',return_value='fake'):
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
                             {'a-faire','cours','actualiser','status','modele'})
            await bot.close()

    async def test_meaningful_text_is_not_randomly_replaced_with_audio(self):
        chat = self.chat()
        chat.bot.voice_samples = [SimpleNamespace(id='cafe')]
        message = self.message()
        with patch('luna_chat.send_voice_message') as send,patch('luna_chat.random.random',return_value=.1):
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
        from haddock_voice import VoiceMessageError
        chat = self.chat()
        chat.bot.voice_samples = [SimpleNamespace(id='cafe')]
        message = self.message()
        choice = {'action':'sample','reply':'Ce café a décidé de prendre le large !','sample_id':'cafe'}
        with patch('luna_chat.random.random',return_value=.1), \
             patch('luna_chat.send_voice_message',return_value={'id':'987'}) as send:
            await chat.deliver(message,choice,True)
            send.assert_called_once()
            message.reply.assert_not_awaited()
            message.channel.send.assert_awaited_once()
            self.assertEqual(message.channel.send.await_args.kwargs['reference'].message_id,987)
            self.assertFalse(message.channel.send.await_args.kwargs['allowed_mentions'].roles)
        chat.bot.last_voice_sample = None
        with patch('luna_chat.random.random',return_value=.1), \
             patch('luna_chat.send_voice_message',side_effect=VoiceMessageError('unavailable')):
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
        with patch('luna_chat.random.random',return_value=.1), \
             patch('luna_chat.send_voice_message',return_value={'id':'987'}):
            await chat.deliver(message,{'action':'sample','reply':'Un café récalcitrant.','sample_id':'cafe'},True)
        message.reply.assert_not_awaited()
        self.assertIn(987,chat.memory.bot_ids)
        self.assertNotIn('PRIVATE_SENTINEL',str(chat.bot.write_status.call_args_list))
        self.assertEqual(chat.bot.write_status.call_args.kwargs['last_luna_reply']['format'],'voice')
        await chat.close()


if __name__ == '__main__': unittest.main()
