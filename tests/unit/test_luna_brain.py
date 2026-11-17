from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timezone
import json
import io
import sqlite3
from pathlib import Path
import tempfile
import unittest
import urllib.error
from unittest.mock import Mock, patch

from luna_brain import (TokenBudget, LunaBrain, BudgetExhausted, LunaError,
                        ChatMemory, ChatCadence, addressed, safe_text, read_api_key)
from courswatch.conversation.persona import PERSONA_TEXT, DISCORD_CONTRACT, PERSONA_VERSION
from courswatch.conversation.metrics import read_model_response


def response(action='text',reply='À bord, camarade !',sample='none',usage=(100,20)):
    return {'status':'completed','usage':{'input_tokens':usage[0],'output_tokens':usage[1]},
            'output':[{'type':'message','content':[{'type':'output_text','text':json.dumps(
                {'action':action,'reply':reply,'sample_id':sample})}]}]}


class LunaTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_quota_counts_input_and_output_and_survives_restart(self):
        ledger = TokenBudget(self.root/'usage.db',limit=100)
        row = ledger.reserve(90,'direct')
        ledger.settle(row,40,30)
        ledger = TokenBudget(self.root/'usage.db',limit=100)
        self.assertEqual(ledger.snapshot()['used_tokens'],70)
        with self.assertRaises(BudgetExhausted):
            ledger.reserve(31,'direct')
        # Once unavailable, even a smaller call stays blocked for the rest of the day.
        with self.assertRaises(BudgetExhausted):
            TokenBudget(self.root/'usage.db',limit=100).reserve(1,'direct')

    def test_bucharest_midnight_resets_usage_and_block_without_sleep(self):
        now = [datetime(2026,11,12,21,59,59,tzinfo=timezone.utc)]
        ledger = TokenBudget(self.root/'usage.db',limit=100,now=lambda:now[0])
        ledger.reserve(100,'direct')
        with self.assertRaises(BudgetExhausted): ledger.reserve(1,'ambient')
        self.assertEqual(ledger.day(),'2026-11-12')
        self.assertEqual(ledger.snapshot()['reset_utc'],'2026-11-12T22:00:00+00:00')
        now[0] = datetime(2026,11,12,22,0,0,tzinfo=timezone.utc)
        self.assertEqual(ledger.day(),'2026-11-13')
        self.assertEqual(ledger.snapshot()['used_tokens'],0)
        self.assertFalse(ledger.snapshot()['blocked'])
        ledger.reserve(100,'direct')

    def test_concurrent_reservations_cannot_overrun_limit(self):
        ledger = TokenBudget(self.root/'usage.db',limit=100)
        def reserve(_):
            try: ledger.reserve(60,'direct'); return True
            except BudgetExhausted: return False
        with ThreadPoolExecutor(max_workers=4) as pool:
            self.assertEqual(sum(pool.map(reserve,range(4))),1)
        self.assertEqual(ledger.snapshot()['used_tokens'],0)
        self.assertEqual(ledger.snapshot()['reserved_tokens'],60)
        self.assertEqual(ledger.snapshot()['budget_used_tokens'],60)

    def test_exhausted_quota_makes_zero_model_requests(self):
        transport = Mock()
        brain = LunaBrain(self.root,{'daily_token_limit':100},api_key='fake',transport=transport)
        with self.assertRaises(BudgetExhausted): brain.decide([],True)
        with self.assertRaises(BudgetExhausted): brain.decide([],False)
        transport.assert_not_called()
        self.assertTrue(brain.budget.snapshot()['blocked'])

    def test_real_payload_contract_and_no_private_text_in_persistent_files(self):
        transport = Mock(return_value=response())
        brain = LunaBrain(self.root,{},api_key='fake',transport=transport)
        result = brain.decide([{'speaker':'Alice','text':'PRIVATE_SENTINEL','is_bot':False}],True)
        self.assertEqual(result['action'],'text')
        payload = transport.call_args.args[0]
        self.assertEqual(payload['model'],'gpt-6-luna')
        self.assertFalse(payload['store'])
        self.assertEqual(payload['reasoning'],{'effort':'none'})
        self.assertTrue(payload['text']['format']['strict'])
        self.assertEqual(brain.budget.snapshot()['used_tokens'],120)
        for path in self.root.rglob('*'):
            if path.is_file(): self.assertNotIn(b'PRIVATE_SENTINEL',path.read_bytes())

    def test_commandcode_uses_its_own_endpoint_key_zdr_and_structured_decision(self):
        brain = LunaBrain(self.root,{'provider':'commandcode'},api_key='cmd_private_synthetic_123456789')
        with patch('luna_brain.urllib.request.urlopen',return_value=io.BytesIO(json.dumps(response()).encode())) as send:
            decision = brain.decide([{'speaker':'Alice','text':'PRIVATE_SENTINEL','is_bot':False}],True)
        self.assertEqual(decision['action'],'text')
        send.assert_called_once()
        request = send.call_args.args[0]
        self.assertEqual(request.full_url,'https://api.commandcode.ai/provider/v1/responses')
        self.assertEqual(request.get_header('Authorization'),'Bearer cmd_private_synthetic_123456789')
        self.assertEqual(request.get_header('X-cmd-zdr'),'1')
        payload = json.loads(request.data)
        self.assertEqual(payload['model'],'deepseek/deepseek-v4.1-flash')
        self.assertFalse(payload['store'])
        self.assertTrue(payload['text']['format']['strict'])
        self.assertEqual(brain.budget.snapshot()['used_tokens'],120)
        report = json.loads((self.root/'reports/luna-status.json').read_text())
        self.assertEqual(report['provider'],'commandcode')
        for path in self.root.rglob('*'):
            if path.is_file():
                content = path.read_bytes()
                self.assertNotIn(b'PRIVATE_SENTINEL',content)
                self.assertNotIn(b'cmd_private_synthetic_123456789',content)

    def test_supplied_character_prompt_reaches_the_model_with_discord_contract(self):
        transport = Mock(return_value=response())
        brain = LunaBrain(self.root,{'provider':'commandcode'},api_key='fake',transport=transport)
        brain.decide([{'speaker':'Alice','text':'Haddock, /jurons rares','is_bot':False}],True)
        payload = transport.call_args.args[0]
        self.assertEqual(payload['instructions'],PERSONA_TEXT.rstrip()+'\n\n'+DISCORD_CONTRACT)
        self.assertEqual(brain.persona_version,PERSONA_VERSION)
        self.assertTrue(payload['text']['format']['strict'])
        self.assertEqual(payload['max_output_tokens'],700)
        report = json.loads((self.root/'reports/luna-status.json').read_text())
        self.assertEqual(report['persona_version'],PERSONA_VERSION)
        self.assertNotIn(PERSONA_TEXT,json.dumps(report))

    def test_commandcode_missing_key_never_falls_back_to_openai(self):
        transport = Mock()
        with patch('luna_brain.read_api_key',return_value='') as read:
            brain = LunaBrain(self.root,{'provider':'commandcode'},transport=transport)
        read.assert_called_once_with(key_name='CMD_API_KEY')
        with self.assertRaises(LunaError) as error:
            brain.decide([],True)
        self.assertEqual(error.exception.code,'missing_key')
        transport.assert_not_called()
        self.assertEqual(brain.budget.snapshot()['calls'],0)

    def test_streaming_request_exposes_measured_stats_without_changing_decision_schema(self):
        final=response(usage=(100,21))
        final['usage'].update(total_tokens=121,input_tokens_details={'cached_tokens':80})
        raw=b'data: '+json.dumps({'type':'response.output_text.delta','delta':'{'}).encode()+b'\n\n'
        raw+=b'data: '+json.dumps({'type':'response.completed','response':final}).encode()+b'\n\n'
        def measured(stream,started):
            times=iter((started+.2,started+.7))
            return read_model_response(stream,started,clock=lambda:next(times))
        brain=LunaBrain(self.root,{'provider':'commandcode'},api_key='fake')
        with patch('luna_brain.urllib.request.urlopen',return_value=io.BytesIO(raw)) as send, \
             patch('luna_brain.read_model_response',side_effect=measured):
            decision=brain.decide([],True)
        self.assertTrue(json.loads(send.call_args.args[0].data)['stream'])
        self.assertEqual(set(decision),{'action','reply','sample_id'})
        self.assertAlmostEqual(decision.metrics['ttft_ms'],200,places=3)
        self.assertAlmostEqual(decision.metrics['api_ms'],700,places=3)
        self.assertAlmostEqual(decision.metrics['output_tps'],40,places=3)
        self.assertEqual(decision.metrics['total_tokens'],121)
        self.assertEqual(brain.budget.snapshot()['used_tokens'],121)
        self.assertEqual(brain.budget.snapshot()['reserved_tokens'],0)

    def test_incomplete_stream_keeps_confirmed_tokens_on_the_local_fallback_error(self):
        final=dict(response(usage=(100,7)),status='incomplete',incomplete_details={'reason':'max_output_tokens'})
        raw=b'data: '+json.dumps({'type':'response.incomplete','response':final}).encode()+b'\n\n'
        brain=LunaBrain(self.root,{'provider':'commandcode'},api_key='fake')
        with patch('luna_brain.urllib.request.urlopen',return_value=io.BytesIO(raw)):
            with self.assertRaises(LunaError) as error:brain.decide([],True)
        self.assertEqual(error.exception.code,'output_limit')
        self.assertEqual(error.exception.metrics['total_tokens'],107)
        self.assertEqual(brain.budget.snapshot()['used_tokens'],107)

    def test_key_reader_selects_only_the_requested_private_env_entry(self):
        env = self.root/'synthetic.env'
        env.write_text('# CMD_API_KEY=comment\nOPENAI_API_KEY="old-fake"\nCMD_API_KEY=\'cmd_fake_only\'\n')
        self.assertEqual(read_api_key(env),'old-fake')
        self.assertEqual(read_api_key(env,key_name='CMD_API_KEY'),'cmd_fake_only')
        self.assertEqual(read_api_key(env,key_name='ABSENT_KEY'),'')

    def test_unknown_provider_is_rejected_before_accessing_keys_or_ledger(self):
        with patch('luna_brain.read_api_key') as read:
            with self.assertRaisesRegex(ValueError,'Unsupported conversation provider'):
                LunaBrain(self.root,{'provider':'https://untrusted.invalid'})
        read.assert_not_called()
        self.assertFalse((self.root/'luna-usage.db').exists())

    def test_provider_and_limit_change_preserve_confirmed_usage(self):
        before = LunaBrain(self.root,{},api_key='fake',transport=lambda _:response())
        before.decide([],True)
        after = LunaBrain(self.root,{'provider':'commandcode','daily_token_limit':10_000_000},
                          api_key='fake',transport=lambda _:response())
        self.assertEqual(after.budget.snapshot()['used_tokens'],120)
        after.decide([],True)
        snapshot = after.budget.snapshot()
        self.assertEqual(snapshot['daily_token_limit'],10_000_000)
        self.assertEqual(snapshot['used_tokens'],240)
        self.assertEqual(snapshot['calls'],2)
        self.assertEqual(snapshot['remaining_tokens'],9_999_760)

    def test_commandcode_refuses_zdr_without_retry_or_provider_fallback(self):
        brain = LunaBrain(self.root,{'provider':'commandcode'},api_key='fake')
        error = urllib.error.HTTPError(brain.endpoint,422,'synthetic',{},None)
        with patch('luna_brain.urllib.request.urlopen',side_effect=error) as send:
            with self.assertRaises(LunaError) as result:
                brain.decide([],True)
        self.assertEqual(result.exception.code,'http_422')
        send.assert_called_once()
        self.assertEqual(send.call_args.args[0].full_url,brain.endpoint)
        self.assertEqual(brain.budget.snapshot()['reserved_tokens'],0)

    def test_commandcode_secret_patterns_are_scrubbed_from_memory(self):
        memory = ChatMemory()
        memory.add('Alice','clé cmd_synthetic_abcdefghijklmnop et cc_synthetic_abcdefghijklmnop')
        content = json.dumps(memory.context())
        self.assertNotIn('abcdefghijklmnop',content)
        self.assertIn('masqu',content)

    def test_timeout_retains_reservation_and_unbilled_error_releases_it(self):
        transport = Mock(side_effect=LunaError('connection_failed'))
        brain = LunaBrain(self.root,{},api_key='fake',transport=transport)
        with self.assertRaises(LunaError): brain.decide([],True)
        reserved = brain.budget.snapshot()['reserved_tokens']
        self.assertGreater(reserved,700)
        transport.side_effect = LunaError('http_401')
        with self.assertRaises(LunaError): brain.decide([],True)
        self.assertEqual(brain.budget.snapshot()['reserved_tokens'],reserved)
        self.assertEqual(brain.budget.snapshot()['used_tokens'],0)

    def test_api_total_is_authoritative_and_cached_input_is_not_added_twice(self):
        result = response(usage=(100,20))
        result['usage'].update(total_tokens=120,input_tokens_details={'cached_tokens':80,'cache_write_tokens':10})
        brain = LunaBrain(self.root,{},api_key='fake',transport=lambda _:result)
        brain.decide([],True)
        snapshot = brain.budget.snapshot()
        self.assertEqual(snapshot['used_tokens'],120)
        self.assertEqual(snapshot['input_tokens'],100)
        self.assertEqual(snapshot['output_tokens'],20)
        self.assertEqual(snapshot['cached_input_tokens'],80)
        self.assertEqual(snapshot['reserved_tokens'],0)
        self.assertEqual(snapshot['remaining_tokens'],999880)

    def test_in_flight_estimate_is_separate_then_replaced_by_confirmed_usage(self):
        ledger = TokenBudget(self.root/'usage.db',limit=100)
        identity = ledger.reserve(90,'direct')
        snapshot = ledger.snapshot()
        self.assertEqual(snapshot['used_tokens'],0)
        self.assertEqual(snapshot['reserved_tokens'],90)
        self.assertEqual(snapshot['remaining_tokens'],10)
        ledger.settle(identity,40,10,api_total_tokens=50)
        snapshot = ledger.snapshot()
        self.assertEqual(snapshot['used_tokens'],50)
        self.assertEqual(snapshot['reserved_tokens'],0)
        self.assertEqual(snapshot['remaining_tokens'],50)

    def test_existing_ledger_migration_preserves_historical_api_usage(self):
        path = self.root/'usage.db'
        with closing(sqlite3.connect(path)) as db:
            db.executescript('''CREATE TABLE calls(id INTEGER PRIMARY KEY,day TEXT,kind TEXT,status TEXT,
                reserved INTEGER,charged INTEGER,input_tokens INTEGER,output_tokens INTEGER,created TEXT);
                INSERT INTO calls VALUES(1,'2026-11-12','direct','ok',900,500,450,50,'2026-11-12T03:00:00+00:00');''')
        ledger = TokenBudget(path,1000,now=lambda:datetime(2026,11,12,4,tzinfo=timezone.utc))
        self.assertEqual(ledger.snapshot()['used_tokens'],500)
        self.assertEqual(ledger.snapshot()['reserved_tokens'],0)
        self.assertEqual(ledger.snapshot()['remaining_tokens'],500)

    def test_invalid_incomplete_and_refusal_still_count_usage(self):
        for code,bad in [('invalid_schema',response(action='execute')),('incomplete_response',dict(response(),status='incomplete')),
                    ('model_refusal',dict(response(),output=[{'type':'message','content':[{'type':'refusal','refusal':'no'}]}]))]:
            brain = LunaBrain(self.root,{},api_key='fake',transport=lambda _:bad)
            with self.assertRaises(LunaError) as error: brain.decide([],True)
            self.assertEqual(error.exception.code,code)
        self.assertEqual(brain.budget.snapshot()['used_tokens'],360)

    def test_refusal_is_not_reported_as_a_network_outage(self):
        result = dict(response(),output=[{'type':'message','content':[{'type':'refusal','refusal':'private text'}]}])
        brain = LunaBrain(self.root,{},api_key='fake',transport=lambda _:result)
        with self.assertRaises(LunaError) as error: brain.decide([],True)
        self.assertEqual(error.exception.code,'model_refusal')
        report = json.loads((self.root/'reports/luna-status.json').read_text())
        self.assertEqual(report['status'],'ready')
        self.assertEqual(report['last_outcome'],'model_refusal')
        self.assertNotIn('private text',json.dumps(report))

    def test_output_limit_and_invalid_json_have_distinct_diagnostics(self):
        cases = [('output_limit',dict(response(),status='incomplete',incomplete_details={'reason':'max_output_tokens'})),
                 ('invalid_json',dict(response(),output=[{'type':'message','content':[{'type':'output_text','text':'{'}]}]))]
        for code,result in cases:
            brain = LunaBrain(self.root,{},api_key='fake',transport=lambda _:result)
            with self.assertRaises(LunaError) as error: brain.decide([],True)
            self.assertEqual(error.exception.code,code)
            report = json.loads((self.root/'reports/luna-status.json').read_text())
            self.assertEqual(report['status'],'degraded')
            self.assertEqual(report['error_code'],code)

    def test_memory_bounds_expiry_and_secrets_are_scrubbed(self):
        now = [10]
        memory = ChatMemory(max_messages=3,ttl_seconds=30,max_bytes=1000,clock=lambda:now[0])
        for index in range(9): memory.add('Alice','x'*500)
        self.assertLessEqual(len(memory.context()),3)
        self.assertLessEqual(len(json.dumps(memory.context()).encode()),1000)
        now[0] = 41
        self.assertEqual(memory.context(),[])
        text = safe_text('sk-proj-abcdefghijklmnop https://discord.com/api/webhooks/123/secret @everyone <@123>')
        self.assertNotIn('abcdefghijklmnop',text)
        self.assertNotIn('/secret',text)
        self.assertNotIn('@everyone',text)
        self.assertNotIn('<@',text)

    def test_direct_address_and_cadence_keep_ambient_quiet(self):
        self.assertTrue(addressed('Salut Haddock, tu vas bien ?'))
        self.assertTrue(addressed('Ça va, Haddock ?'))
        self.assertFalse(addressed('Le capitaine Haddock est dans ce livre.'))
        self.assertTrue(addressed('Tu en penses quoi ?',reply_to_bot=True))
        now = [1000]
        cadence = ChatCadence(clock=lambda:now[0])
        self.assertTrue(cadence.ambient_allowed())
        cadence.sent(True,1)
        self.assertTrue(cadence.followup(1))
        self.assertFalse(cadence.followup(2))
        self.assertFalse(cadence.ambient_allowed())
        now[0] += 121
        self.assertFalse(cadence.followup(1))
        for _ in range(4):
            self.assertTrue(cadence.ambient_allowed())
            cadence.sent(False)
            now[0] += 181
        self.assertFalse(cadence.ambient_allowed())
        now[0] += 3600
        self.assertTrue(cadence.ambient_allowed())


if __name__ == '__main__': unittest.main()
