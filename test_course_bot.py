import json
import functools
import inspect
import io
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
import discord
from unittest.mock import AsyncMock, MagicMock, patch

from bot_ui_data import find_event, remember_private_card
from cours_watch import State
from course_bot import CourseBot, DetailsView, TaskListView


class BotTests(unittest.IsolatedAsyncioTestCase):
    def config(self):
        return {'discord':{'guild_id':'1376984512046563519','channel_id':'1570209088929800282',
                           'application_id':'1510767148006641866','role_ids':['1570209160744673411']}}

    async def test_commands_are_scoped_to_the_course_server_and_no_privileged_intents(self):
        with patch('course_bot.configuration',return_value=self.config()):
            bot = CourseBot('fake-not-a-token')
            commands = bot.tree.get_commands(guild=bot.scope)
            self.assertEqual({c.name for c in commands},{'cours','actualiser','status','a-faire'})
            self.assertFalse(bot.intents.message_content)
            self.assertFalse(bot.intents.members)
            self.assertFalse(bot.intents.presences)
            self.assertTrue(DetailsView(bot).is_persistent())
            for command in commands:
                self.assertEqual(command.to_dict(bot.tree)['name'],command.name)
            await bot.close()

    async def test_unauthorized_member_gets_no_course_details(self):
        interaction = SimpleNamespace(guild_id=1376984512046563519,channel_id=1570209088929800282,
            user=SimpleNamespace(roles=[SimpleNamespace(id=999)]),response=SimpleNamespace(send_message=AsyncMock()))
        with patch('course_bot.configuration',return_value=self.config()):
            bot = CourseBot('fake-not-a-token')
            self.assertFalse(await bot.authorize(interaction))
            self.assertTrue(interaction.response.send_message.await_args.kwargs['ephemeral'])
            await bot.close()

    async def test_button_response_is_private_including_the_initial_defer(self):
        client = SimpleNamespace(authorize=AsyncMock(return_value=True),write_status=MagicMock())
        interaction = SimpleNamespace(message=SimpleNamespace(id=123),user=SimpleNamespace(id=456),
            response=SimpleNamespace(defer=AsyncMock()),followup=SimpleNamespace(send=AsyncMock()))
        event = {'course':'SOS','title':'Détails','body':'Consignes de la présentation','url':'https://curs.upb.ro/'}
        with patch('course_bot.find_event',return_value=event):
            await DetailsView(client).details.callback(interaction)
        self.assertTrue(interaction.response.defer.await_args.kwargs['ephemeral'])
        self.assertTrue(interaction.followup.send.await_args.kwargs['ephemeral'])
        self.assertIn('Consignes',interaction.followup.send.await_args.args[0])

    async def test_private_card_snapshot_is_bound_to_the_requesting_user(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = State(root / 'state.db')
            state.db.close()
            event = {'course':'AAIT','body':'Private snapshot'}
            remember_private_card(root,123,456,event)
            self.assertEqual(find_event(root,123,456),event)
            self.assertIsNone(find_event(root,123,999))

    async def test_status_response_is_private_and_does_not_trigger_a_collection(self):
        interaction = SimpleNamespace(guild_id=1376984512046563519,channel_id=1570209088929800282,
            user=SimpleNamespace(roles=[SimpleNamespace(id=1570209160744673411)]),
            response=SimpleNamespace(defer=AsyncMock()),followup=SimpleNamespace(send=AsyncMock()))
        with patch('course_bot.configuration',return_value=self.config()), \
             patch('course_bot.build_status_snapshot',return_value={}) as inspect, \
             patch('course_bot.status_embed',return_value={'title':'État du service'}):
            bot = CourseBot('fake-not-a-token')
            bot.write_status = MagicMock()
            command = bot.tree.get_command('status',guild=bot.scope)
            await command.callback(interaction)
            self.assertTrue(interaction.response.defer.await_args.kwargs['ephemeral'])
            self.assertTrue(interaction.followup.send.await_args.kwargs['ephemeral'])
            inspect.assert_called_once()
            self.assertIsNone(bot.child)
            await bot.close()

    async def test_gateway_resume_refreshes_health(self):
        with patch('course_bot.configuration',return_value=self.config()):
            bot = CourseBot('fake-not-a-token')
            bot.on_ready = AsyncMock()
            await bot.on_resumed()
            bot.on_ready.assert_awaited_once()
            await bot.close()

    async def test_task_command_is_private_filtered_and_does_not_collect_or_ping(self):
        interaction = SimpleNamespace(guild_id=1376984512046563519,channel_id=1570209088929800282,
            user=SimpleNamespace(id=456,roles=[SimpleNamespace(id=1570209160744673411)]),
            response=SimpleNamespace(defer=AsyncMock()),followup=SimpleNamespace(send=AsyncMock()))
        pages = [{'title':'SOS · À faire','description':'Échéances connues'}]
        with patch('course_bot.configuration',return_value=self.config()), \
             patch('course_bot.current_task_pages',return_value=pages) as current:
            bot = CourseBot('fake-not-a-token')
            await bot.tree.get_command('a-faire',guild=bot.scope).callback(interaction,'SOS')
            current.assert_called_once_with(unittest.mock.ANY,self.config(),'SOS')
            self.assertTrue(interaction.response.defer.await_args.kwargs['ephemeral'])
            sent = interaction.followup.send.await_args.kwargs
            self.assertTrue(sent['ephemeral'])
            self.assertFalse(sent['allowed_mentions'].everyone)
            self.assertFalse(sent['allowed_mentions'].roles)
            self.assertIsNone(bot.child)
            self.assertTrue(sent['view'].previous.disabled)
            self.assertTrue(sent['view'].next.disabled)
            await bot.close()

    async def test_task_pages_update_the_same_response_and_recheck_owner_and_access(self):
        client = SimpleNamespace(authorize=AsyncMock(return_value=True))
        view = TaskListView(client,456,[{'title':'Page 1'},{'title':'Page 2'}])
        intruder = SimpleNamespace(user=SimpleNamespace(id=999),response=SimpleNamespace(send_message=AsyncMock()))
        self.assertFalse(await view.interaction_check(intruder))
        client.authorize.assert_not_awaited()
        interaction = SimpleNamespace(user=SimpleNamespace(id=456),response=SimpleNamespace(edit_message=AsyncMock()))
        self.assertTrue(await view.interaction_check(interaction))
        client.authorize.assert_awaited_once()
        await view.next.callback(interaction)
        self.assertEqual(interaction.response.edit_message.await_args.kwargs['embed'].title,'Page 2')
        self.assertTrue(view.next.disabled)
        self.assertFalse(view.previous.disabled)
        await view.previous.callback(interaction)
        self.assertEqual(view.page,0)
        view.stop()

    async def test_task_storage_error_returns_a_private_explanation(self):
        import sqlite3
        interaction = SimpleNamespace(user=SimpleNamespace(id=456),response=SimpleNamespace(defer=AsyncMock()),
                                      followup=SimpleNamespace(send=AsyncMock()))
        with patch('course_bot.configuration',return_value=self.config()), \
             patch('course_bot.current_task_pages',side_effect=sqlite3.OperationalError('private path')):
            bot = CourseBot('fake-not-a-token')
            bot.authorize = AsyncMock(return_value=True)
            await bot.tree.get_command('a-faire',guild=bot.scope).callback(interaction)
            self.assertTrue(interaction.followup.send.await_args.kwargs['ephemeral'])
            self.assertNotIn('private path',interaction.followup.send.await_args.args[0])
            await bot.close()

    def egg_config(self):
        config = self.config()
        config['discord']['easter_egg'] = {'enabled':True,'cooldown_seconds':30}
        return config

    def message(self,**changes):
        values = {'id':1570251511731191880,'guild':SimpleNamespace(id=1376984512046563519),
                  'channel':SimpleNamespace(id=1570209088929800282),
                  'author':SimpleNamespace(bot=False,roles=[SimpleNamespace(id=1570209160744673411)]),
                  'webhook_id':None,'mentions':[],'content':'Haddock !',
                  'reply':AsyncMock(return_value=SimpleNamespace(id=123))}
        values.update(changes)
        return SimpleNamespace(**values)

    async def test_easter_egg_replies_without_ping_and_throttles(self):
        with patch('course_bot.configuration',return_value=self.egg_config()):
            bot = CourseBot('fake-not-a-token')
            bot.write_status = MagicMock()
            self.assertTrue(bot.intents.guild_messages)
            self.assertTrue(bot.intents.message_content)
            self.assertFalse(bot.intents.dm_messages)
            self.assertFalse(bot.intents.members)
            self.assertFalse(bot.intents.presences)
            message = self.message()
            await bot.on_message(message)
            await bot.on_message(message)
            message.reply.assert_awaited_once()
            self.assertFalse(message.reply.await_args.kwargs['mention_author'])
            self.assertFalse(message.reply.await_args.kwargs['allowed_mentions'].everyone)
            self.assertNotIn('content',str(bot.write_status.call_args))
            await bot.close()

    async def test_easter_egg_ignores_bots_webhooks_other_channels_and_unrelated_messages(self):
        with patch('course_bot.configuration',return_value=self.egg_config()):
            bot = CourseBot('fake-not-a-token')
            cases = [self.message(author=SimpleNamespace(bot=True)),self.message(webhook_id=123),
                     self.message(guild=None),self.message(channel=SimpleNamespace(id=99)),
                     self.message(author=SimpleNamespace(bot=False,roles=[])),self.message(content='Rendu demain')]
            for message in cases:
                await bot.on_message(message)
                message.reply.assert_not_awaited()
            await bot.close()

    async def test_easter_egg_is_disabled_unless_configured(self):
        with patch('course_bot.configuration',return_value=self.config()):
            bot = CourseBot('fake-not-a-token')
            message = self.message()
            await bot.on_message(message)
            message.reply.assert_not_awaited()
            await bot.close()

    async def test_easter_egg_uses_real_discord_reply_with_supported_send_parameters(self):
        with patch('course_bot.configuration',return_value=self.egg_config()):
            bot = CourseBot('fake-not-a-token')
            bot.write_status = MagicMock()
            message = self.message()
            async def strict_send(content=None,**kwargs):
                inspect.signature(discord.abc.Messageable.send).bind(message.channel,content,**kwargs)
                return SimpleNamespace(id=321)
            message.channel.send = AsyncMock(side_effect=strict_send)
            message.reply = functools.partial(discord.Message.reply,message)
            await bot.on_message(message)
            message.channel.send.assert_awaited_once()
            self.assertIs(message.channel.send.await_args.kwargs['reference'],message)
            self.assertFalse(message.channel.send.await_args.kwargs['mention_author'])
            await bot.close()

    async def test_callback_errors_log_type_without_private_event_or_exception_text(self):
        with patch('course_bot.configuration',return_value=self.egg_config()):
            bot = CourseBot('fake-not-a-token')
            bot.write_status = MagicMock()
            output = io.StringIO()
            error = TypeError('private user text')
            with patch('course_bot.sys.exc_info',return_value=(TypeError,error,None)), redirect_stdout(output):
                await bot.on_error('on_message','private event content')
            self.assertIn('TypeError',output.getvalue())
            self.assertNotIn('private',output.getvalue())
            self.assertNotIn('private',str(bot.write_status.call_args))
            await bot.close()

    async def test_easter_egg_selected_half_sends_voice_without_text(self):
        cfg = self.egg_config()
        cfg['discord']['easter_egg']['voice_probability'] = 0.5
        with patch('course_bot.configuration',return_value=cfg), \
             patch('course_bot.load_samples',return_value=[SimpleNamespace(id='sample')]), \
             patch('course_bot.random.random',return_value=0.1), \
             patch('course_bot.send_voice_message',return_value={'id':'987'}) as send:
            bot = CourseBot('fake-not-a-token')
            bot.write_status = MagicMock()
            message = self.message()
            await bot.on_message(message)
            message.reply.assert_not_awaited()
            send.assert_called_once()
            self.assertEqual(send.call_args.kwargs['reference_id'],str(message.id))
            self.assertEqual(bot.write_status.call_args.kwargs['last_easter_egg']['format'],'voice')
            await bot.close()

    async def test_easter_egg_voice_failure_falls_back_to_text(self):
        from haddock_voice import VoiceMessageError
        cfg = self.egg_config()
        cfg['discord']['easter_egg']['voice_probability'] = 0.5
        with patch('course_bot.configuration',return_value=cfg), \
             patch('course_bot.load_samples',return_value=[SimpleNamespace(id='sample')]), \
             patch('course_bot.random.random',return_value=0.1), \
             patch('course_bot.send_voice_message',side_effect=VoiceMessageError('unavailable')):
            bot = CourseBot('fake-not-a-token')
            bot.write_status = MagicMock()
            message = self.message()
            await bot.on_message(message)
            message.reply.assert_awaited_once()
            self.assertEqual(bot.write_status.call_args.kwargs['last_easter_egg']['format'],'text')
            await bot.close()

    async def test_easter_egg_other_half_stays_text(self):
        cfg = self.egg_config()
        cfg['discord']['easter_egg']['voice_probability'] = 0.5
        with patch('course_bot.configuration',return_value=cfg), \
             patch('course_bot.load_samples',return_value=[SimpleNamespace(id='sample')]), \
             patch('course_bot.random.random',return_value=0.9), \
             patch('course_bot.send_voice_message') as send:
            bot = CourseBot('fake-not-a-token')
            bot.write_status = MagicMock()
            message = self.message()
            await bot.on_message(message)
            message.reply.assert_awaited_once()
            send.assert_not_called()
            await bot.close()


if __name__ == '__main__':
    unittest.main()
