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
from courswatch.conversation.models import effective_settings, save_selection
from course_bot import CourseBot


class ModelTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.settings = {'provider':'commandcode','model':'deepseek/deepseek-v4.1-flash',
                         'daily_token_limit':10_000_000}

    async def asyncTearDown(self):
        self.temp.cleanup()

    async def test_selection_survives_restart_and_cannot_override_keys_or_quota(self):
        save_selection(self.root,'hy4')
        actual = effective_settings(self.root,self.settings)
        self.assertEqual(actual['model'],'tencent/hy4-flash')
        self.assertFalse(actual['require_zdr'])
        self.assertEqual(actual['daily_token_limit'],10_000_000)
        self.assertEqual(json.loads((self.root/'model-selection.json').read_text()),{'selection':'hy4'})
        with self.assertRaises(ValueError):save_selection(self.root,'https://untrusted.invalid')
        self.assertEqual(effective_settings(self.root,self.settings),actual)
        (self.root/'model-selection.json').write_text('{"selection":"hy4","require_zdr":false}')
        with self.assertRaises(ValueError):effective_settings(self.root,self.settings)

    async def test_failed_atomic_write_preserves_selection(self):
        save_selection(self.root,'deepseek')
        with patch('courswatch.conversation.models.os.replace',side_effect=OSError('synthetic')):
            with self.assertRaises(OSError):save_selection(self.root,'hy4')
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
            pending=asyncio.create_task(chat.switch_model('hy4'))
            await asyncio.sleep(0)
            self.assertFalse(pending.done())
            self.assertEqual(chat.brain.model,self.settings['model'])
        await pending
        self.assertEqual(chat.brain.model,'tencent/hy4-flash')
        self.assertEqual(chat.retry_after,0)
        self.assertEqual(chat.brain.budget.snapshot()['used_tokens'],15)
        self.assertIn('PRIVATE_RAM_SENTINEL',str(chat.memory.context()))
        self.assertNotIn('PRIVATE_RAM_SENTINEL',(self.root/'model-selection.json').read_text())
        await chat.switch_model('deepseek')
        self.assertTrue(chat.brain.require_zdr)
        self.assertEqual(chat.brain.budget.snapshot()['used_tokens'],15)
        await chat.close()

    async def test_command_enforces_admin_scope_and_private_no_mentions(self):
        cfg={'discord':{'guild_id':'1','channel_id':'2','role_ids':['3'],'application_id':'4'}}
        interaction=SimpleNamespace(guild_id=1,channel_id=2,user=SimpleNamespace(
            roles=[SimpleNamespace(id=3)],guild_permissions=SimpleNamespace(administrator=False)),
            response=SimpleNamespace(send_message=AsyncMock(),defer=AsyncMock(),is_done=lambda:True),
            followup=SimpleNamespace(send=AsyncMock()))
        with patch('course_bot.configuration',return_value=cfg):
            bot=CourseBot('fake')
            bot.write_status=MagicMock()
            brain=SimpleNamespace(provider='commandcode',model='tencent/hy4-flash',require_zdr=False)
            bot.luna=SimpleNamespace(brain=brain,switch_model=AsyncMock(),close=AsyncMock())
            command=bot.tree.get_command('modele',guild=bot.scope)
            self.assertTrue(command.default_permissions.administrator)
            await command.callback(interaction,'hy4')
            bot.luna.switch_model.assert_not_awaited()
            self.assertTrue(interaction.response.send_message.await_args.kwargs['ephemeral'])
            interaction.user.guild_permissions.administrator=True
            interaction.channel_id=999
            await command.callback(interaction,'hy4')
            bot.luna.switch_model.assert_not_awaited()
            interaction.channel_id=2
            await command.callback(interaction,'hy4')
            bot.luna.switch_model.assert_awaited_once_with('hy4')
            sent=interaction.followup.send.await_args
            self.assertTrue(sent.kwargs['ephemeral'])
            self.assertFalse(sent.kwargs['allowed_mentions'].roles)
            self.assertIn('ZDR non exigé',sent.args[0])
            await bot.close()
