"""Participation, accounting and privacy boundaries on synthetic messages."""
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from courswatch.conversation.brain import LunaBrain, LunaError
from courswatch.conversation.decision import participation_vote, JEV


class ParticipationModelTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.brain = LunaBrain(Path(temporary.name), {'model':'tencent/hy4-flash','provider':'commandcode'},
                               api_key='test')
        self.profile = self.brain.capture_profile()
        message = SimpleNamespace(text='Et SOS ?',author_id='sensitive-id',is_bot=False)
        self.snapshot = SimpleNamespace(goal=message.text, messages=[message],requester_ids=(message.author_id,))

    def vote(self, before=lambda: True):
        return participation_vote(self.brain,self.snapshot,True,self.profile,before)

    def test_valid_vote_is_metered_without_identifiers(self):
        reply = {'answers':{'respond':{'type':'noul','noul':0.9}},
                 'usage':{'input_tokens':100,'output_tokens':2}}
        with patch('urllib.request.urlopen',return_value=io.BytesIO(json.dumps(reply).encode())) as send:
            answer,model,call_id=self.vote()
        self.assertTrue(answer)
        self.assertEqual(model,JEV.requested_model_id)
        request=send.call_args.args[0]
        self.assertEqual(request.full_url,JEV.endpoint)
        self.assertNotIn(b'sensitive-id',request.data)
        self.assertEqual(self.brain.budget.snapshot()['used_tokens'],102)
        self.assertIsInstance(call_id,int)

    def test_revoked_before_dispatch_skips_reservation(self):
        with patch('urllib.request.urlopen') as send,self.assertRaises(LunaError):
            self.vote(lambda:False)
        send.assert_not_called()
        self.assertEqual(self.brain.budget.snapshot()['calls'],0)

    def test_bad_score_abstains_and_keeps_confirmed_usage(self):
        reply={'answers':{'respond':{'type':'noul','noul':'yes'}},'usage':{'input_tokens':65,'output_tokens':3}}
        with patch('urllib.request.urlopen',return_value=io.BytesIO(json.dumps(reply).encode())), self.assertRaises(LunaError):
            self.vote()
        self.assertEqual(self.brain.budget.snapshot()['used_tokens'],68)

    def test_zdr_uses_active_generation_route(self):
        self.brain = LunaBrain(self.brain.data,{'model':'deepseek/deepseek-v4.1-flash','provider':'commandcode'},
                               api_key='test')
        self.profile=self.brain.capture_profile()
        response={'status':'completed','output':[{'type':'message','content':[{'type':'output_text','text':'{"respond":false}'}]}]}
        with patch.object(self.brain,'_execute',return_value=(response,{'call_id':12})) as execute, patch('urllib.request.urlopen') as send:
            self.assertEqual(self.vote(),(False,self.profile.requested_model_id,12))
        self.assertTrue(execute.call_args.args[1].require_zdr)
        send.assert_not_called()
