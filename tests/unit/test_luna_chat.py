import asyncio
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from course_bot import CourseBot
from luna_brain import BUDGET_REPLY, BudgetExhausted, ChatMemory, ChatCadence, LunaError, OFFLINE_REPLY, REFUSAL_REPLY
from luna_chat import LunaChat


class ChatTests(unittest.IsolatedAsyncioTestCase):
    def message(self,**changes):
        data = dict(id=123,guild=SimpleNamespace(id=1),channel=SimpleNamespace(id=2),
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
        chat.memory,chat.cadence = ChatMemory(),ChatCadence()
        chat.pending_direct = chat.pending_ambient = chat.task = None
        chat.closed,chat.sequence = False,0
        chat.retry_after = 0
        return chat

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

    async def test_quota_returns_static_phrase_and_commands_still_exist(self):
        chat = self.chat()
        chat.brain.decide.side_effect = BudgetExhausted()
        message = self.message()
        chat.accept(message,False)
        await chat.task
        self.assertEqual(message.reply.await_args.args[0],BUDGET_REPLY)
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
                             {'a-faire','cours','actualiser','status'})
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
        self.assertNotEqual(first.reply.await_args.args[0],OFFLINE_REPLY)
        self.assertEqual(chat.retry_after,0)
        chat.accept(second,False)
        await chat.task
        self.assertEqual(chat.brain.decide.call_count,2)
        self.assertEqual(second.reply.await_args.args[0],'Je t’écoute, forban.')
        await chat.close()

    async def test_refusal_is_local_to_that_request_and_is_not_retried(self):
        chat = self.chat()
        chat.brain.decide.side_effect = LunaError('model_refusal')
        message = self.message()
        chat.accept(message,False)
        await chat.task
        self.assertEqual(message.reply.await_args.args[0],REFUSAL_REPLY)
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
        chat.bot.last_voice_sample = None
        with patch('luna_chat.random.random',return_value=.1), \
             patch('luna_chat.send_voice_message',side_effect=VoiceMessageError('unavailable')):
            await chat.deliver(message,choice,True)
            self.assertEqual(message.reply.await_args.args[0],choice['reply'])
        await chat.close()


if __name__ == '__main__': unittest.main()
