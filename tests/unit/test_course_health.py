from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest
from zoneinfo import ZoneInfo

from courswatch.runtime import Item, Result, State
from courswatch.discord_app.health import build_status_snapshot, status_embed
from courswatch.conversation.brain import TokenBudget, BudgetExhausted


class HealthTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        (self.root / 'reports').mkdir()
        self.state = State(self.root / 'state.db')
        self.state.ingest(Result('moodle:AAIT','AAIT','ok',[Item('module-1:dates','Crafter',
            'Due: Sunday, 15 November 2026, 11:59 PM','https://curs.upb.ro/2026/mod/assign/view.php?id=1')]))
        self.state.ingest(Result('teams:AAIT','AAIT','ok',[]))
        self.reminders = self.root / 'reminders.json'
        self.reminders.write_text(json.dumps({'enabled':True,'policy':{'send_hour':8},'tasks':[
            {'id':'crafter','course':'AAIT','title':'Crafter','date_mode':'moodle_due','action':'Code et slides',
             'url':'https://curs.upb.ro/2026/mod/assign/view.php?id=1',
             'reference':{'source':'moodle:AAIT','key':'module-1:dates'}}]}))
        self.config = {'courses':[{'id':'AAIT'}], 'reminders_file':str(self.reminders),
                       'discord':{'guild_id':'1','channel_id':'2','mode':'bot_channel'}}
        self.now = datetime(2026,11,12,7,tzinfo=ZoneInfo('Europe/Bucharest'))

    def tearDown(self):
        self.state.db.close()
        self.directory.cleanup()

    def units(self,now):
        next_check = now.replace(hour=8,minute=0,second=0,microsecond=0)
        if next_check <= now:
            next_check += timedelta(days=1)
        return {'cours-watch-bot.service':{'ActiveState':'active','SubState':'running'},
                'cours-watch.timer':{'ActiveState':'active','NextElapseUSecRealtime':'@' + str(next_check.timestamp())},
                'cours-watch-session.timer':{'ActiveState':'active'},
                'cours-watch-session.service':{'ActiveState':'inactive','Result':'success'},
                'cours-watch.service':{'ActiveState':'inactive','Result':'success','ExecMainStatus':'0'}}

    def snapshot(self,now=None,units=None,session_age=3):
        now = now or self.now
        (self.root / 'reports/latest.json').write_text(json.dumps({'checked_utc':(now - timedelta(minutes=5)).isoformat()}))
        (self.root / 'reports/moodle-session.json').write_text(json.dumps({'status':'ok','checked_utc':(now - timedelta(minutes=session_age)).isoformat()}))
        return build_status_snapshot(self.root,self.config,True,float('nan'),now=now,
                                     units=units if units is not None else self.units(now),gate_ready=True)

    def enqueue_point(self,prefix):
        self.state.enqueue(prefix + ':2026-11-12:AAIT:0',{'kind':'daily_reminder','course':'AAIT','body':'Rappel'})
        return self.state.pending()[-1][0]

    def confirm(self,event_id):
        self.state.mark_destination(event_id,'bot-channel:2',{'id':'123','channel_id':'2','mention_roles':[]})
        self.state.mark_complete(event_id)

    def test_future_point_is_not_reported_missing_and_history_has_real_receipt(self):
        self.confirm(self.enqueue_point('initial-brief'))
        snapshot = self.snapshot()
        self.assertEqual(snapshot['today']['state'],'upcoming')
        self.assertEqual(snapshot['issues'],[])
        self.assertEqual(snapshot['batches'][0]['confirmed'],1)
        self.assertTrue(snapshot['batches'][0]['url'].endswith('/2/123'))
        card = status_embed(snapshot)
        self.assertIn('08:00',str(card))
        self.assertIn('12/11',str(card))
        self.assertIsNone(snapshot['latency_ms'])

    def test_pending_daily_message_cannot_be_marked_done(self):
        event_id = self.enqueue_point('daily-reminder')
        now = self.now.replace(hour=9)
        snapshot = self.snapshot(now)
        self.assertEqual(snapshot['pending'],1)
        self.assertEqual(snapshot['today']['state'],'unconfirmed')
        self.confirm(event_id)
        delivered = self.snapshot(now)
        self.assertEqual(delivered['pending'],0)
        self.assertEqual(delivered['today']['state'],'confirmed')

    def test_disabled_timer_does_not_promise_the_stale_next_time(self):
        units = self.units(self.now)
        units['cours-watch.timer']['ActiveState'] = 'inactive'
        snapshot = self.snapshot(units=units)
        self.assertFalse(snapshot['automatic'])
        self.assertIsNone(snapshot['next_check_utc'])
        self.assertIn('désactivée',str(status_embed(snapshot)))

    def test_failed_source_is_visible_even_if_last_report_was_successful(self):
        self.state.ingest(Result('moodle:AAIT','AAIT','auth_required',[],'Connexion à rétablir'))
        snapshot = self.snapshot()
        self.assertEqual(snapshot['sources']['moodle']['ok'],0)
        self.assertEqual(snapshot['sources']['teams']['ok'],1)
        self.assertTrue(snapshot['issues'])

    def test_failed_scheduled_attempt_is_not_hidden_by_an_older_good_report(self):
        units = self.units(self.now)
        units['cours-watch.service'].update(Result='exit-code',ExecMainStatus='3',
            ExecMainExitTimestamp='@' + str((self.now - timedelta(minutes=1)).timestamp()))
        self.assertIn('Dernier passage programmé en échec',self.snapshot(units=units)['issues'])

    def test_expired_tasks_do_not_create_a_missing_daily_announcement(self):
        snapshot = self.snapshot(self.now.replace(day=16,hour=9))
        self.assertEqual(snapshot['today']['expected'],0)
        self.assertEqual(snapshot['today']['state'],'not_required')
        self.assertNotIn('Rappel du jour non confirmé',snapshot['issues'])

    def test_in_progress_collection_is_not_reported_as_a_late_announcement(self):
        now = self.now.replace(hour=9)
        units = self.units(now)
        units['cours-watch.service']['ActiveState'] = 'activating'
        snapshot = self.snapshot(now,units=units)
        self.assertEqual(snapshot['today']['state'],'preparing')
        self.assertIn('Collecte en cours',str(status_embed(snapshot)))

    def test_stale_moodle_session_is_flagged(self):
        self.assertIn('Maintien de session Moodle à vérifier',self.snapshot(session_age=60)['issues'])

    def test_actual_updates_are_included_in_the_confirmed_announcement_history(self):
        self.state.ingest(Result('teams:AAIT','AAIT','ok',[Item('post-1','Nouveau rendu','Consignes','https://teams.cloud.microsoft/')]))
        event_id = self.state.pending()[-1][0]
        self.confirm(event_id)
        snapshot = self.snapshot()
        self.assertEqual(snapshot['batches'][0]['kind'],'updates')
        self.assertEqual(snapshot['batches'][0]['confirmed'],1)

    def test_commandcode_status_displays_provider_model_and_ten_million_ceiling(self):
        self.config['discord']['luna'] = {'enabled':True,'provider':'commandcode',
                                         'model':'deepseek/deepseek-v4.1-flash',
                                         'daily_token_limit':10_000_000}
        ledger = TokenBudget(self.root/'luna-usage.db',10_000_000,now=lambda:self.now)
        identity = ledger.reserve(100,'direct')
        ledger.settle(identity,80,20)
        (self.root/'reports/luna-status.json').write_text(json.dumps({'status':'ready','provider':'openai','model':'old'}))
        snapshot = self.snapshot()
        self.assertEqual(snapshot['luna']['provider'],'commandcode')
        self.assertEqual(snapshot['luna']['model'],'deepseek/deepseek-v4.1-flash')
        self.assertEqual(snapshot['luna']['used_tokens'],100)
        self.assertEqual(snapshot['luna']['daily_token_limit'],10_000_000)
        card = str(status_embed(snapshot))
        self.assertIn('CommandCode',card)
        self.assertIn('100 / 10 000 000',card)
        self.assertIn('Conversations · Haddock',card)

    def test_provider_rejection_marks_overall_health_until_recovery(self):
        self.config['discord']['luna'] = {'enabled': True, 'provider': 'commandcode',
                                          'model': 'tencent/hy4-flash'}
        TokenBudget(self.root/'luna-usage.db', 10_000, now=lambda: self.now)
        report = self.root/'reports/luna-status.json'
        report.write_text(json.dumps({'status': 'error', 'error_code': 'http_400'}))
        snapshot = self.snapshot()
        self.assertIn('Conversations avec Haddock à vérifier', snapshot['issues'])
        self.assertEqual(status_embed(snapshot)['color'], 0xF39C12)
        report.write_text(json.dumps({'status': 'ready'}))
        self.assertEqual(self.snapshot()['issues'], [])

    def test_luna_status_reads_today_ledger_without_changing_it_and_resets_at_midnight(self):
        self.config['discord']['luna'] = {'enabled':True,'daily_token_limit':100,
                                         'provider':'commandcode','model':'deepseek/deepseek-v4.1-flash'}
        ledger = TokenBudget(self.root/'luna-usage.db',100,now=lambda:self.now)
        identity = ledger.reserve(100,'direct')
        ledger.settle(identity,80,20)
        with self.assertRaises(BudgetExhausted): ledger.reserve(1,'direct')
        (self.root/'reports/luna-status.json').write_text(json.dumps({'status':'daily_limit'}))
        before = (self.root/'luna-usage.db').read_bytes()
        snapshot = self.snapshot()
        self.assertEqual(snapshot['luna']['used_tokens'],100)
        self.assertIn('cale sèche',str(status_embed(snapshot)))
        self.assertEqual((self.root/'luna-usage.db').read_bytes(),before)
        tomorrow = self.snapshot(self.now + timedelta(days=1))
        self.assertEqual(tomorrow['luna']['used_tokens'],0)
        self.assertEqual(tomorrow['luna']['status'],'ready')

    def test_luna_status_does_not_present_an_in_flight_reservation_as_consumption(self):
        self.config['discord']['luna'] = {'enabled':True,'daily_token_limit':100,
                                         'provider':'commandcode','model':'tencent/hy4-flash'}
        ledger = TokenBudget(self.root/'luna-usage.db',100,now=lambda:self.now)
        ledger.reserve(90,'direct')
        (self.root/'reports/luna-status.json').write_text(json.dumps({'status':'ready'}))
        snapshot = self.snapshot()
        self.assertEqual(snapshot['luna']['used_tokens'],0)
        self.assertEqual(snapshot['luna']['reserved_tokens'],90)
        card = status_embed(snapshot)
        self.assertIn('0 / 100',str(card))
        self.assertIn('Réservés / non confirmés',str(card))

    def test_invalid_profile_does_not_report_a_fallback_model(self):
        self.config['discord']['luna'] = {'enabled':True,'provider':'commandcode'}
        (self.root/'reports/luna-status.json').write_text(
            json.dumps({'status':'ready','provider':'openai','model':'gpt-6-luna'}))
        snapshot = self.snapshot()
        self.assertEqual(snapshot['luna'], {'status':'invalid_config'})
        card = str(status_embed(snapshot))
        self.assertIn('Configuration du modèle invalide', card)
        self.assertNotIn('gpt-6-luna', card)


if __name__ == '__main__':
    unittest.main()
