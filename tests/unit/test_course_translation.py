from copy import deepcopy
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from courswatch.translation import eligible, cache_key, prepare_events, with_cached_translation
from courswatch.discord_app.presentation import compact_embed, private_details
from courswatch.runtime import State, notify


class TranslationTests(unittest.TestCase):
    def event(self):
        return {'kind':'new','course':'AAIT','source':'teams:AAIT','title':'Project presentation',
                'body':'Please upload the slides by November 15. @everyone','url':'https://teams.cloud.microsoft/'}

    def worker(self,request):
        return [{'key':item['key'],'status':'translated','source_language':'en','target_language':'fr',
                 'title':'Présentation du projet','body':'Déposez les diapositives avant le 15 novembre. @everyone'} for item in request['items']]

    def test_only_actual_messages_are_selected(self):
        event = self.event()
        self.assertTrue(eligible(event))
        self.assertFalse(eligible({**event,'title':'Meeting terminé'}))
        self.assertFalse(eligible({**event,'source':'moodle:AAIT','url':'https://curs.upb.ro/2026/mod/resource/view.php?id=1'}))
        self.assertTrue(eligible({**event,'source':'moodle:AAIT','url':'https://curs.upb.ro/2026/mod/forum/discuss.php?d=1'}))
        self.assertFalse(eligible({**event,'kind':'daily_reminder'}))

    def test_cache_reuses_translation_and_never_mutates_original(self):
        event = self.event()
        original = deepcopy(event)
        with tempfile.TemporaryDirectory() as directory, patch('courswatch.translation.settings',return_value={'enabled':True}):
            root = Path(directory)
            first = prepare_events([event],root,self.worker)[0]
            second = prepare_events([event],root,lambda request:self.fail('Cache was not reused'))[0]
            self.assertEqual(event,original)
            self.assertEqual(first['body'],original['body'])
            self.assertEqual(second['translation']['body'],first['translation']['body'])
            self.assertEqual(with_cached_translation(event,root)['translation'],first['translation'])
            self.assertNotEqual(cache_key(event),cache_key({**event,'body':'Updated announcement'}))

    def test_failed_engine_keeps_original_and_can_retry(self):
        with tempfile.TemporaryDirectory() as directory, patch('courswatch.translation.settings',return_value={'enabled':True}):
            root = Path(directory)
            def broken(request):
                raise OSError('unavailable')
            failed = prepare_events([self.event()],root,broken)[0]
            self.assertEqual(failed['translation']['status'],'failed')
            self.assertEqual(failed['body'],self.event()['body'])
            self.assertIn('Texte original affiché',compact_embed(failed)['footer']['text'])
            fixed = prepare_events([self.event()],root,self.worker)[0]
            self.assertEqual(fixed['translation']['status'],'translated')

    def test_default_worker_uses_module_with_explicit_cwd_and_filtered_environment(self):
        result = SimpleNamespace(stdout='[]')
        with tempfile.TemporaryDirectory() as directory, \
                patch('courswatch.translation.settings', return_value={'enabled': True}), \
                patch('courswatch.translation.subprocess.run', return_value=result) as run:
            prepare_events([self.event()], Path(directory))
        self.assertEqual(run.call_args.args[0],
                         ['/opt/cours-watch/translation-venv/bin/python', '-m',
                          'courswatch.translation_worker'])
        self.assertEqual(run.call_args.kwargs['cwd'], Path(__file__).resolve().parents[2])
        self.assertNotIn('PYTHONPATH', run.call_args.kwargs['env'])
        self.assertEqual(set(run.call_args.kwargs['env']),
                         {'PATH', 'LANG', 'OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS'})

    def test_public_card_is_french_and_private_details_keep_both_versions_without_pings(self):
        with tempfile.TemporaryDirectory() as directory, patch('courswatch.translation.settings',return_value={'enabled':True}):
            event = prepare_events([self.event()],Path(directory),self.worker)[0]
            card = compact_embed(event,interactive=True)
            self.assertIn('Présentation',card['title'])
            self.assertIn('Déposez',card['description'])
            self.assertNotIn('Please upload',json.dumps(card))
            details = '\n'.join(private_details(event))
            self.assertIn('Déposez',details)
            self.assertIn('Please upload',details)
            self.assertIn('Texte original',details)
            self.assertNotIn('@everyone',details)

    def test_notify_sends_french_but_retains_raw_outbox_and_snapshot(self):
        with tempfile.TemporaryDirectory() as directory, patch('courswatch.translation.settings',return_value={'enabled':True}):
            root = Path(directory)
            state = State(root / 'state.db')
            try:
                event = self.event()
                state.enqueue('new-message',event)
                prepare_events([event],root,self.worker)
                config = {'mode':'bot_channel','channel_id':'1570209088929800282','user_ids':[],
                          'role_ids':['1570209160744673411'],'presentation':'embed','interactive':True}
                with patch('courswatch.runtime.discord_request',return_value={'id':'1570230953404665994','channel_id':config['channel_id']}) as request:
                    notify(state,config,{'DISCORD_BOT_TOKEN':'fake'})
                payload = request.call_args.args[1]
                self.assertIn('Déposez',payload['embeds'][0]['description'])
                self.assertEqual(json.loads(state.db.execute('SELECT payload FROM outbox').fetchone()[0]),event)
            finally:
                state.db.close()
