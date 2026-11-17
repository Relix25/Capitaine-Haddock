"""Offline contract tests: synthetic capability records do not certify providers."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import FrozenInstanceError, replace
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from courswatch.conversation.brain import BudgetExhausted, LunaBrain, LunaError, TokenBudget
from courswatch.conversation.models import (ENDPOINT, MODEL_CHOICES, resolve_profile,
                                             settings_for_selection)


def settings(profile='deepseek', **extra):
    return {'provider':'commandcode', **MODEL_CHOICES[profile], **extra}


def synthetic_reviewed_record(profile='deepseek', **extra):
    """Fictitious review identity used only for exercising the gated adapter."""
    return {'provider':'commandcode', 'endpoint':ENDPOINT,
            'requested_model_id':MODEL_CHOICES[profile]['model'],
            'require_zdr':MODEL_CHOICES[profile]['require_zdr'],
            'image_input':'verified','multi_image':'verified','structured_output':'verified',
            'api_image_input':'verified','api_structured_output':'verified',
            'verified_real':True,'evidence':'SYNTHETIC TEST RECORD - not a provider verification',**extra}


def response(value=None, **extra):
    value = value or {'action':'text','reply':'À bord !','sample_id':'none'}
    return {'status':'completed','usage':{'input_tokens':10,'output_tokens':5},
            'output':[{'type':'message','content':[{'type':'output_text','text':json.dumps(value)}]}],**extra}


class ModelProfileTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)

    def test_missing_obsolete_or_unknown_profiles_fail_before_key_and_database(self):
        for config in ({}, {'provider':'commandcode'}, {'provider':'openai','model':'gpt-6-luna'},
                       settings(model='deepseek/deepseek-v4.1-flash-latest'),settings(require_zdr=False)):
            with self.subTest(config=config),patch('courswatch.conversation.brain.read_api_key') as read:
                with self.assertRaises(ValueError):LunaBrain(self.root,config)
                read.assert_not_called()
                self.assertFalse((self.root/'luna-usage.db').exists())

    def test_owner_vision_declaration_is_distinct_from_unverified_api_combination(self):
        for name in MODEL_CHOICES:
            transport=Mock()
            brain=LunaBrain(self.root,settings(name),api_key='fake',transport=transport)
            profile=brain.capture_profile()
            self.assertEqual(profile.capabilities.image_input,'declared')
            self.assertEqual(profile.capabilities.api_image_input,'unknown')
            self.assertFalse(profile.capabilities.verified_real)
            self.assertIsNone(brain.vision_profile(profile))
            with self.assertRaises(LunaError) as error:
                brain.describe_media([{'at_ms':0,'data':b'fake png','mime_type':'image/png'}],profile)
            self.assertEqual(error.exception.code,'capability_unavailable')
            transport.assert_not_called()
            self.assertEqual(brain.budget.snapshot()['calls'],0)

    def test_verified_record_requires_exact_model_endpoint_json_and_privacy_evidence(self):
        for change in ({'evidence':''},{'endpoint':'https://elsewhere.invalid'},
                       {'requested_model_id':'tencent/hy4-flash'},{'require_zdr':False},
                       {'api_structured_output':'unknown'},{'provider':'openai'}):
            with self.subTest(change=change),self.assertRaises(ValueError):
                resolve_profile(settings(model_capabilities={'deepseek':synthetic_reviewed_record(**change)}))
        for name in MODEL_CHOICES:
            record = synthetic_reviewed_record(name)
            del record['evidence']
            with self.subTest(profile=name), self.assertRaises(ValueError):
                resolve_profile(settings(name, model_capabilities={name: record}))

    def test_incomplete_total_only_settles_the_matching_call(self):
        result = response(status='incomplete', usage={'total_tokens':9876,
            'input_tokens_details':'malformed optional metadata'})
        brain = LunaBrain(self.root, settings(), api_key='fake', transport=lambda _:result)
        with self.assertRaises(LunaError) as error:
            brain.decide([], True)
        metrics = error.exception.metrics
        self.assertEqual(error.exception.code, 'incomplete_response')
        self.assertEqual(metrics['total_tokens'], 9876)
        self.assertIsNone(metrics['input_tokens'])
        self.assertIsNone(metrics['output_tokens'])
        self.assertFalse(metrics['unknown_usage'])
        self.assertEqual(brain.budget.snapshot()['used_tokens'], 9876)
        with closing(brain.budget.connect()) as db:
            saved = db.execute('SELECT charged,api_total_tokens FROM calls WHERE id=?',
                               (metrics['call_id'],)).fetchone()
        self.assertEqual(saved, (9876, 9876))

    def test_vision_provision_excludes_base64_and_reports_the_active_profile(self):
        records = {name:synthetic_reviewed_record(name) for name in MODEL_CHOICES}
        config = settings(model_capabilities=records, daily_token_limit=100_000)
        observation = {'visual_description':'Fixture.', 'visible_text':[],
                       'animation_summary':'','limitations':[]}
        transport = Mock(return_value=response(observation))
        brain = LunaBrain(self.root, config, api_key='fake', transport=transport)
        captured = brain.vision_profile()
        candidate = settings_for_selection({'provider':'commandcode','model_capabilities':records}, 'hy4', False)
        brain.use_profile(candidate, resolve_profile(candidate))
        result = brain.describe_media([{'at_ms':0,'data':b'x'*500_000,'mime_type':'image/png'}], captured)
        self.assertEqual(result['metrics']['requested_model_id'], MODEL_CHOICES['deepseek']['model'])
        transport.assert_called_once()
        with closing(brain.budget.connect()) as db:
            reserved = db.execute('SELECT reserved FROM calls').fetchone()[0]
        self.assertLess(reserved, 25_000)
        report = json.loads((self.root/'reports/luna-status.json').read_text())
        self.assertEqual(report['model'], MODEL_CHOICES['hy4']['model'])
        self.assertEqual(report['last_call_profile']['requested_model_id'], MODEL_CHOICES['deepseek']['model'])

    def test_shared_admission_prioritizes_text_and_rechecks_before_dispatch(self):
        first_entered = threading.Event()
        first_exits = threading.Event()
        visits = []
        observation = {'visual_description':'Fixture.', 'visible_text':[],
                       'animation_summary':'','limitations':[]}

        def transport(payload):
            kind = payload['text']['format']['name']
            visits.append(kind)
            if len(visits) == 1:
                first_entered.set()
                self.assertTrue(first_exits.wait(3))
            return response(observation) if kind == 'haddock_media_observation' else response()

        brain = LunaBrain(self.root, settings(model_capabilities={'deepseek':synthetic_reviewed_record()}),
                          api_key='fake',transport=transport)
        profile = brain.vision_profile()
        frames = [{'at_ms':0,'data':b'fixture','mime_type':'image/png'}]
        queue_trace = threading.Event()
        original_wait = brain._admission.wait_for

        def wait_for(predicate, timeout=None):
            if len(brain._admission_waiters) == 2:
                queue_trace.set()
            return original_wait(predicate, timeout)

        def revoked():
            self.assertEqual(visits, ['haddock_media_observation','haddock_decision'])
            return False

        with patch.object(brain._admission, 'wait_for', side_effect=wait_for):
            with ThreadPoolExecutor(max_workers=3) as workers:
                active = workers.submit(brain.describe_media, frames, profile)
                try:
                    self.assertTrue(first_entered.wait(3))
                    cancelled = workers.submit(brain.describe_media, frames, profile,
                                               before_dispatch=revoked)
                    text = workers.submit(brain.decide, [], True)
                    self.assertTrue(queue_trace.wait(3))
                    self.assertEqual(visits, ['haddock_media_observation'])
                    self.assertEqual(brain.budget.snapshot()['calls'], 1)
                finally:
                    first_exits.set()
                self.assertGreaterEqual(text.result(timeout=3).metrics['provider_queue_ms'], 0)
                active.result(timeout=3)
                with self.assertRaises(LunaError) as error:
                    cancelled.result(timeout=3)
                self.assertEqual(error.exception.code, 'request_invalidated')
                self.assertNotIn('call_id', error.exception.metrics)
        self.assertEqual(visits, ['haddock_media_observation','haddock_decision'])
        self.assertEqual(brain.budget.snapshot()['calls'], 2)

    def test_frozen_request_profile_preserves_headers_usage_and_unknown_revision_after_switch(self):
        brain=LunaBrain(self.root,settings(profile_config_version='configuration-A'),api_key='fake')
        original=brain.capture_profile()
        with self.assertRaises(FrozenInstanceError):original.require_zdr=False
        def send(payload):
            brain.model=MODEL_CHOICES['hy4']['model'];brain.require_zdr=False
            brain.config={**brain.config,**MODEL_CHOICES['hy4'],'profile_config_version':'configuration-B'}
            return brain.request(payload,profile=original)
        brain.transport=send
        final=response(model='provider-resolved-model')
        with patch('courswatch.conversation.brain.urllib.request.urlopen',return_value=io.BytesIO(json.dumps(final).encode())) as http:
            decision=brain.decide([],True,profile=original)
        self.assertEqual(http.call_args.args[0].get_header('X-cmd-zdr'),'1')
        self.assertEqual(decision.metrics['requested_model_id'],original.requested_model_id)
        self.assertEqual(decision.metrics['profile_config_version'],'configuration-A')
        self.assertIsNone(decision.metrics['resolved_model_revision'])
        with closing(sqlite3.connect(self.root/'luna-usage.db')) as db:
            record=db.execute('SELECT profile_id,requested_model_id,resolved_model_id,resolved_model_revision,config_version,require_zdr FROM call_profiles').fetchone()
        self.assertEqual(record,('deepseek',original.requested_model_id,'provider-resolved-model',None,'configuration-A',1))
        self.assertEqual(brain.budget.snapshot()['used_tokens'],15)

    def test_explicit_cross_profile_route_cannot_relax_a_turn_zdr_requirement(self):
        records={name:synthetic_reviewed_record(name) for name in MODEL_CHOICES}
        strong=LunaBrain(self.root,settings(model_capabilities=records,vision_route='hy4'),api_key='fake')
        self.assertIsNone(strong.vision_profile())
        weak=LunaBrain(self.root,settings('hy4',model_capabilities=records,vision_route='deepseek'),api_key='fake')
        self.assertEqual(weak.vision_profile().profile_id,'deepseek')
        self.assertTrue(weak.vision_profile().require_zdr)
        records['hy4'] = synthetic_reviewed_record('hy4', require_zdr=True)
        strong = LunaBrain(self.root, settings(model_capabilities=records, vision_route='hy4'), api_key='fake')
        self.assertIsNone(strong.vision_profile())
        strong.config = {**strong.config, 'vision_route_require_zdr':True}
        self.assertTrue(strong.vision_profile().require_zdr)

    def test_synthetic_reviewed_record_serializes_fixed_frames_strict_json_and_zdr(self):
        observation={'visual_description':'Un panneau fictif.',
                     'visible_text':[{'text':'TEST','at_ms':1200,'quality':'readable'}],
                     'animation_summary':'Deux instants observés.','limitations':['Échantillonnage partiel.']}
        transport=Mock(return_value=response(observation,model='resolved',model_revision='revision-supplied'))
        brain=LunaBrain(self.root,settings(model_capabilities={'deepseek':synthetic_reviewed_record()}),
                        api_key='fake',transport=transport)
        profile=brain.vision_profile()
        result=brain.describe_media([{'at_ms':0,'data':b'first','mime_type':'image/png'},
                                     {'at_ms':1200,'data':b'second','mime_type':'image/png'}],profile)
        payload=transport.call_args.args[0]
        self.assertTrue(payload['text']['format']['strict']);self.assertFalse(payload['store'])
        images=[p for p in payload['input'][0]['content'] if p['type']=='input_image']
        self.assertEqual(len(images),2)
        self.assertTrue(all(p['image_url'].startswith('data:image/png;base64,') for p in images))
        self.assertEqual(result['provenance']['resolved_model_revision'],'revision-supplied')
        self.assertEqual(brain.budget.snapshot()['used_tokens'],15)

    def test_animation_and_payload_limits_fail_locally_without_call_or_charge(self):
        transport=Mock()
        brain=LunaBrain(self.root,settings(model_capabilities={'deepseek':synthetic_reviewed_record(max_payload_bytes=1024)}),
                        api_key='fake',transport=transport)
        profile=brain.vision_profile()
        for frame,code in (({'at_ms':0,'data':b'GIF89a','mime_type':'image/gif'},'invalid_media'),
                           ({'at_ms':0,'data':b'x'*3000,'mime_type':'image/png'},'payload_too_large')):
            with self.assertRaises(LunaError) as error:brain.describe_media([frame],profile)
            self.assertEqual(error.exception.code,code)
        transport.assert_not_called();self.assertEqual(brain.budget.snapshot()['calls'],0)

    def test_vision_throttle_protects_text_and_deadline_is_not_extended_by_rechecks(self):
        transport=Mock(side_effect=LunaError('http_429'))
        brain=LunaBrain(self.root,settings(model_capabilities={'deepseek':synthetic_reviewed_record()}),
                        api_key='fake',transport=transport)
        now=[100.0];brain.clock=lambda:now[0]
        with self.assertRaises(LunaError):
            brain.describe_media([{'at_ms':0,'data':b'x','mime_type':'image/png'}],brain.vision_profile())
        self.assertEqual(brain.retry_after,160)
        now[0]=120
        with self.assertRaises(LunaError) as error:brain.decide([],True)
        self.assertEqual(error.exception.code,'circuit_open')
        self.assertEqual(brain.retry_after,160);self.assertEqual(transport.call_count,1)
        now[0]=160;transport.side_effect=None;transport.return_value=response()
        self.assertTrue(brain.can_call());brain.decide([],True)
        self.assertEqual(transport.call_count,2)

    def test_scoped_vision_payload_rejection_keeps_text_route_and_releases_hold(self):
        transport=Mock(side_effect=LunaError('http_422'))
        brain=LunaBrain(self.root,settings(model_capabilities={'deepseek':synthetic_reviewed_record()}),
                        api_key='fake',transport=transport)
        with self.assertRaises(LunaError):
            brain.describe_media([{'at_ms':0,'data':b'x','mime_type':'image/png'}],brain.vision_profile())
        self.assertTrue(brain.can_call());self.assertEqual(brain.budget.snapshot()['reserved_tokens'],0)
        transport.side_effect=None;transport.return_value=response()
        brain.decide([],True);self.assertEqual(brain.budget.snapshot()['used_tokens'],15)

    def test_malformed_provider_response_retains_uncertain_hold_without_fake_usage(self):
        brain=LunaBrain(self.root,settings(),api_key='fake',transport=lambda _:None)
        with self.assertRaises(LunaError) as error:brain.decide([],True)
        self.assertEqual(error.exception.code,'invalid_response')
        self.assertTrue(error.exception.metrics['unknown_usage'])
        self.assertGreater(brain.budget.snapshot()['reserved_tokens'],0)
        self.assertTrue(brain.can_call())

    def test_unconfigured_cross_profile_description_cannot_disclose_media(self):
        records={name:synthetic_reviewed_record(name) for name in MODEL_CHOICES}
        transport=Mock()
        brain=LunaBrain(self.root,settings(model_capabilities=records),api_key='fake',transport=transport)
        other=resolve_profile(settings('hy4',model_capabilities=records))
        with self.assertRaises(LunaError) as error:
            brain.describe_media([{'at_ms':0,'data':b'x','mime_type':'image/png'}],other)
        self.assertEqual(error.exception.code,'policy_mismatch')
        transport.assert_not_called()

    def test_media_profile_and_route_remain_frozen_while_settings_change(self):
        records={name:synthetic_reviewed_record(name) for name in MODEL_CHOICES}
        observation={'visual_description':'Fixture.', 'visible_text':[],
                     'animation_summary':'','limitations':[]}
        brain=LunaBrain(self.root,settings(model_capabilities=records,profile_config_version='old'),
                        api_key='fake',transport=lambda _:response(observation))
        captured=brain.capture_profile()
        brain.model=MODEL_CHOICES['hy4']['model'];brain.require_zdr=False
        brain.config={**brain.config,**MODEL_CHOICES['hy4'],'vision_route':'hy4','profile_config_version':'new'}
        visual=brain.vision_profile(captured)
        self.assertEqual(visual.profile_id,'deepseek');self.assertTrue(visual.require_zdr)
        self.assertEqual(visual.config_version,'old')
        result=brain.describe_media([{'at_ms':0,'data':b'fixture','mime_type':'image/png'}],visual)
        self.assertEqual(result['provenance']['requested_model_id'],MODEL_CHOICES['deepseek']['model'])
        self.assertEqual(result['provenance']['profile_config_version'],'old')

    def test_equal_profiles_keep_distinct_captured_routing_and_eviction_never_recaptures(self):
        records={name:synthetic_reviewed_record(name) for name in MODEL_CHOICES}
        brain=LunaBrain(self.root,settings('hy4',model_capabilities=records,vision_route='deepseek'),api_key='fake')
        before=brain.capture_profile()
        brain.config={**brain.config,'vision_route':'hy4'}
        after=brain.capture_profile()
        self.assertEqual(before,after);self.assertIsNot(before,after)
        self.assertEqual(brain.vision_profile(before).profile_id,'deepseek')
        self.assertEqual(brain.vision_profile(after).profile_id,'hy4')
        for _ in range(65):brain.capture_profile()
        self.assertIsNone(brain.vision_profile(before))

    def test_deferred_background_reservation_preserves_smaller_direct_and_legacy_migration(self):
        path=self.root/'luna-usage.db'
        with closing(sqlite3.connect(path)) as db:
            db.executescript('''CREATE TABLE calls(id INTEGER PRIMARY KEY,day TEXT,kind TEXT,status TEXT,
                reserved INTEGER,charged INTEGER,input_tokens INTEGER,output_tokens INTEGER,created TEXT);
                INSERT INTO calls VALUES(1,'2026-11-17','direct','ok',60,60,50,10,'2026-11-17');''')
        ledger=TokenBudget(path,100,interactive_reserve=10)
        with self.assertRaises(BudgetExhausted):ledger.reserve(101,'vision')
        direct=ledger.reserve(1,'direct');self.assertIsInstance(direct,int)
        self.assertFalse(ledger.snapshot()['blocked'])
        with closing(sqlite3.connect(path)) as db:
            self.assertEqual(db.execute('SELECT charged FROM calls WHERE id=1').fetchone()[0],60)
            self.assertEqual(db.execute("SELECT MAX(version) FROM schema_migrations WHERE component='budget'").fetchone()[0],2)

    def test_full_hold_releases_capacity_after_settlement_without_sticky_day_block(self):
        ledger=TokenBudget(self.root/'usage.db',100)
        held=ledger.reserve(100,'direct')
        with self.assertRaises(BudgetExhausted):ledger.reserve(1,'direct')
        self.assertFalse(ledger.snapshot()['blocked'])
        self.assertEqual(ledger.snapshot()['reserved_tokens'],100)
        with closing(ledger.connect()) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM blocked_days').fetchone()[0],0)
        ledger.settle(held,7,3)
        smaller=ledger.reserve(1,'direct')
        self.assertIsInstance(smaller,int)
        self.assertEqual(ledger.snapshot()['used_tokens'],10)
        self.assertEqual(ledger.snapshot()['remaining_tokens'],89)
        self.assertFalse(ledger.snapshot()['blocked'])

    def test_confirmed_exhaustion_blocks_day_and_existing_markers_are_preserved(self):
        ledger=TokenBudget(self.root/'usage.db',100)
        held=ledger.reserve(100,'direct');ledger.settle(held,90,10)
        with self.assertRaises(BudgetExhausted):ledger.reserve(1,'direct')
        self.assertTrue(ledger.snapshot()['blocked'])
        with closing(ledger.connect()) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM blocked_days').fetchone()[0],1)
        # An inherited block remains explicit policy/history even if a later
        # operator changes the configured limit; this code never resets it.
        reopened=TokenBudget(self.root/'usage.db',1000)
        with self.assertRaises(BudgetExhausted):reopened.reserve(1,'direct')
        self.assertTrue(reopened.snapshot()['blocked'])

    def test_concurrent_status_and_calls_keep_distinct_profile_records(self):
        brain=LunaBrain(self.root,settings(),api_key='fake',transport=lambda _:response())
        profiles=[brain.capture_profile(),resolve_profile(settings('hy4'))]
        with ThreadPoolExecutor(max_workers=4) as pool:
            results=list(pool.map(lambda i:brain.decide([],True,profile=profiles[i%2]),range(8)))
        self.assertEqual(len(results),8)
        with closing(sqlite3.connect(self.root/'luna-usage.db')) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM call_profiles').fetchone()[0],8)
        self.assertEqual(brain.budget.snapshot()['used_tokens'],120)
        self.assertEqual(list((self.root/'reports').glob('.luna-status-*')),[])
        json.loads((self.root/'reports/luna-status.json').read_text())


if __name__=='__main__':unittest.main()
