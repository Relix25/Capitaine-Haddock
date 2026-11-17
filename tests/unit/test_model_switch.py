import asyncio
import io
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from courswatch.conversation.brain import LunaBrain
from courswatch.conversation.chat import LunaChat
from courswatch.conversation.models import effective_settings, save_preferences
from courswatch.discord_app.settings_ui import SettingsModal
from courswatch.discord_app.bot import CourseBot


class ModelTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.settings = {'provider':'commandcode','model':'deepseek/deepseek-v4.1-flash',
                         'daily_token_limit':10_000_000}

    async def asyncTearDown(self):
        self.temp.cleanup()

    async def test_selection_survives_restart_and_cannot_override_keys_or_quota(self):
        save_preferences(self.root,'hy4',False)
        actual = effective_settings(self.root,self.settings)
        self.assertEqual(actual['model'],'tencent/hy4-flash')
        self.assertFalse(actual['require_zdr'])
        self.assertEqual(actual['daily_token_limit'],10_000_000)
        self.assertFalse(actual['debug'])
        self.assertEqual(json.loads((self.root/'model-selection.json').read_text()),{'selection':'hy4','debug':False})
        with self.assertRaises(ValueError):save_preferences(self.root,'https://untrusted.invalid',False)
        self.assertEqual(effective_settings(self.root,self.settings),actual)
        (self.root/'model-selection.json').write_text('{"selection":"hy4"}')
        self.assertTrue(effective_settings(self.root,self.settings)['debug'])
        (self.root/'model-selection.json').write_text('{"selection":"hy4","debug":"false"}')
        with self.assertRaises(ValueError):effective_settings(self.root,self.settings)
        (self.root/'model-selection.json').write_text('{"selection":"hy4","require_zdr":false}')
        with self.assertRaises(ValueError):effective_settings(self.root,self.settings)

    async def test_failed_atomic_write_preserves_selection(self):
        save_preferences(self.root,'deepseek',True)
        with patch('courswatch.conversation.models.os.replace',side_effect=OSError('synthetic')):
            with self.assertRaises(OSError):save_preferences(self.root,'hy4',False)
        self.assertEqual(effective_settings(self.root,self.settings)['model'],self.settings['model'])
        self.assertEqual(len(list(self.root.iterdir())),1)

    async def test_hy4_omits_zdr_only_when_explicit_and_keeps_store_false(self):
        with patch('courswatch.conversation.brain.read_api_key',return_value='fake'):
            chat = LunaChat(SimpleNamespace(),self.root,{**self.settings,'require_zdr':False,'model':'tencent/hy4-flash'},lambda:{})
        output={'status':'completed','usage':{'input_tokens':10,'output_tokens':5},
                'output':[{'type':'message','content':[{'type':'output_text','text':json.dumps(
                    {'action':'text','reply':'À bord !','sample_id':'none'})}]}]}
        with patch('courswatch.conversation.brain.urllib.request.urlopen',return_value=io.BytesIO(json.dumps(output).encode())) as send:
            chat.brain.decide([],True)
        request=send.call_args.args[0]
        self.assertIsNone(request.get_header('X-cmd-zdr'))
        self.assertFalse(json.loads(request.data)['store'])
        self.assertEqual(chat.brain.budget.snapshot()['used_tokens'],15)
        with self.assertRaises(ValueError):
            LunaBrain(self.root,{**self.settings,'require_zdr':'false'},api_key='fake')
        await chat.close()

    async def test_switch_waits_for_generation_and_preserves_memory_and_ledger(self):
        with patch('courswatch.conversation.brain.read_api_key',return_value='fake'):
            chat=LunaChat(SimpleNamespace(),self.root,self.settings,lambda:{})
        chat.memory.add('Alice','PRIVATE_RAM_SENTINEL')
        row=chat.brain.budget.reserve(30,'direct');chat.brain.budget.settle(row,10,5)
        chat.retry_after=999999999999
        async with chat.model_lock:
            pending=asyncio.create_task(chat.apply_settings('hy4',False))
            await asyncio.sleep(0)
            self.assertFalse(pending.done())
            self.assertEqual(chat.brain.model,self.settings['model'])
        await pending
        self.assertEqual(chat.brain.model,'tencent/hy4-flash')
        self.assertFalse(chat.debug_enabled)
        # A model change cannot bypass an existing provider cooldown.
        self.assertEqual(chat.retry_after,999999999999)
        self.assertEqual(chat.brain.budget.snapshot()['used_tokens'],15)
        self.assertIn('PRIVATE_RAM_SENTINEL',str(chat.memory.context()))
        self.assertNotIn('PRIVATE_RAM_SENTINEL',(self.root/'model-selection.json').read_text())
        await chat.apply_settings('deepseek',True)
        self.assertTrue(chat.brain.require_zdr)
        self.assertTrue(chat.debug_enabled)
        self.assertEqual(chat.brain.budget.snapshot()['used_tokens'],15)
        await chat.close()

    def interaction(self, **changes):
        done=[False]
        async def sent(*args,**kwargs):done[0]=True
        result=SimpleNamespace(guild_id=1,channel_id=2,user=SimpleNamespace(id=10,
            roles=[SimpleNamespace(id=3)],guild_permissions=SimpleNamespace(administrator=True)),
            response=SimpleNamespace(send_message=AsyncMock(side_effect=sent),defer=AsyncMock(side_effect=sent),
                send_modal=AsyncMock(side_effect=sent),is_done=lambda:done[0]),
            followup=SimpleNamespace(send=AsyncMock()))
        for key,value in changes.items():setattr(result,key,value)
        return result

    def config(self):
        return {'discord':{'guild_id':'1','channel_id':'2','role_ids':['3'],'application_id':'4'}}

    async def test_command_enforces_admin_scope_and_opens_prefilled_modal(self):
        with patch('courswatch.discord_app.bot.configuration',return_value=self.config()):
            bot=CourseBot('fake')
            bot.write_status=MagicMock()
            brain=SimpleNamespace(provider='commandcode',model='tencent/hy4-flash',require_zdr=False)
            bot.luna=SimpleNamespace(brain=brain,debug_enabled=False,apply_settings=AsyncMock(),close=AsyncMock())
            command=bot.tree.get_command('settings',guild=bot.scope)
            self.assertTrue(command.default_permissions.administrator)
            interaction=self.interaction()
            interaction.user.guild_permissions.administrator=False
            await command.callback(interaction)
            interaction.response.send_modal.assert_not_awaited()
            self.assertTrue(interaction.response.send_message.await_args.kwargs['ephemeral'])
            interaction=self.interaction(channel_id=999)
            await command.callback(interaction)
            interaction.response.send_modal.assert_not_awaited()
            interaction=self.interaction()
            await command.callback(interaction)
            modal=interaction.response.send_modal.await_args.args[0]
            data=modal.to_dict()
            self.assertEqual([item['type'] for item in data['components']],[18,18])
            model,debug=[item['component'] for item in data['components']]
            self.assertEqual([item['value'] for item in model['options'] if item['default']],['hy4'])
            self.assertEqual([item['value'] for item in debug['options'] if item['default']],['off'])
            self.assertIsNone(bot.tree.get_command('modele',guild=bot.scope))
            bot.luna.apply_settings.assert_not_awaited()
            modal.stop()
            await bot.close()

    async def test_modal_dispatch_saves_both_preferences_and_reopens_with_current_values(self):
        with patch('courswatch.discord_app.bot.configuration',return_value=self.config()), \
             patch('courswatch.conversation.brain.read_api_key',return_value='fake'):
            bot=CourseBot('fake');bot.write_status=MagicMock()
            bot.luna=LunaChat(bot,self.root,self.settings,lambda:{})
            modal=SettingsModal(bot,10)
            interaction=self.interaction()
            await modal._scheduled_task(interaction,[
                {'type':18,'component':{'type':3,'custom_id':modal.model.custom_id,'values':['hy4']}},
                {'type':18,'component':{'type':3,'custom_id':modal.debug.custom_id,'values':['off']}},
            ],{})
            self.assertEqual(bot.luna.brain.model,'tencent/hy4-flash')
            self.assertFalse(bot.luna.debug_enabled)
            self.assertEqual(effective_settings(self.root,self.settings)['debug'],False)
            sent=interaction.followup.send.await_args
            self.assertTrue(sent.kwargs['ephemeral'])
            self.assertFalse(sent.kwargs['allowed_mentions'].roles)
            self.assertIn('Debug : désactivé',sent.args[0])
            self.assertEqual(bot.luna.brain.budget.snapshot()['calls'],0)
            reopened=SettingsModal(bot,10)
            self.assertTrue(reopened.model.options[0].default)
            self.assertTrue(reopened.debug.options[1].default)
            reopened.stop()
            await bot.close()

    async def test_submit_rechecks_owner_role_and_admin_permission(self):
        with patch('courswatch.discord_app.bot.configuration',return_value=self.config()):
            bot=CourseBot('fake')
            bot.luna=SimpleNamespace(brain=SimpleNamespace(provider='commandcode',model='tencent/hy4-flash'),
                debug_enabled=True,apply_settings=AsyncMock(),close=AsyncMock())
            for change in ('owner','role','admin','channel'):
                modal=SettingsModal(bot,10);interaction=self.interaction()
                if change=='owner':interaction.user.id=11
                if change=='role':interaction.user.roles=[]
                if change=='admin':interaction.user.guild_permissions.administrator=False
                if change=='channel':interaction.channel_id=99
                await modal.on_submit(interaction)
                bot.luna.apply_settings.assert_not_awaited()
                self.assertTrue(interaction.response.send_message.await_args.kwargs['ephemeral'])
                modal.stop()
            await bot.close()

    async def test_settings_write_failure_changes_neither_live_model_nor_debug(self):
        with patch('courswatch.conversation.brain.read_api_key',return_value='fake'):
            chat=LunaChat(SimpleNamespace(),self.root,self.settings,lambda:{})
        with patch('courswatch.conversation.models.os.replace',side_effect=OSError('synthetic')):
            with self.assertRaises(OSError):await chat.apply_settings('hy4',False)
        self.assertEqual(chat.brain.model,self.settings['model'])
        self.assertTrue(chat.debug_enabled)
        self.assertFalse((self.root/'model-selection.json').exists())
        await chat.close()

    async def test_registration_removes_only_retired_chatbot_command(self):
        cfg=self.config()
        def request(url,payload,token,method='POST'):
            if method=='GET':return [{'id':'100','name':'modele','type':1},
                {'id':'101','name':'unrelated','type':1},{'id':'102','name':'modele','type':2}]
            return {}
        with patch('courswatch.discord_app.bot.configuration',return_value=cfg),patch('courswatch.discord_app.bot.discord_request',side_effect=request) as send:
            bot=CourseBot('fake')
            await bot.setup_hook()
            deletes=[call for call in send.call_args_list if call.kwargs.get('method')=='DELETE']
            self.assertEqual(len(deletes),1)
            self.assertTrue(deletes[0].args[0].endswith('/100'))
            self.assertTrue(bot.commands_registered)
            await bot.close()
