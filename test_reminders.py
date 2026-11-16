from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest
from zoneinfo import ZoneInfo

from cours_watch import Item, Result, State
from course_reminders import body_hash, enqueue_daily_reminders, parse_moodle_due, reminder_events, urgency


class ReminderTests(unittest.TestCase):
    def setUp(self):
        self.state = State(':memory:')
        self.tz = ZoneInfo('Europe/Bucharest')
        self.now = datetime(2026, 11, 12, 8, tzinfo=self.tz)
        self.state.ingest(Result('moodle:AAIT', 'AAIT', 'ok', [Item('module-1:dates', 'Crafter',
            'Opened: Friday, 9 October 2026, 12:00 AM Due: Sunday, 15 November 2026, 11:59 PM',
            'https://curs.upb.ro/2026/mod/assign/view.php?id=1')]))
        self.task = {'id':'crafter','course':'AAIT','title':'Crafter','date_mode':'moodle_due',
                     'action':'Code et slides','url':'https://curs.upb.ro/2026/mod/assign/view.php?id=1',
                     'reference':{'source':'moodle:AAIT','key':'module-1:dates'}}

    def tearDown(self):
        self.state.db.close()

    def test_moodle_midnight_is_start_of_day_not_end(self):
        due = parse_moodle_due('Due: Tuesday, 17 November 2026, 12:00 AM', self.tz)
        self.assertEqual((due.day, due.hour, due.minute), (17, 0, 0))

    def test_urgency_and_elapsed_deadline(self):
        self.assertIn('IMPORTANT', urgency(self.now + timedelta(days=3), self.now, True)[1])
        self.assertIn('URGENT', urgency(self.now + timedelta(days=1), self.now, True)[1])
        self.assertIsNone(urgency(self.now - timedelta(minutes=1), self.now, True))

    def test_daily_reminders_repeat_next_day_but_not_same_day(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'reminders.json'
            path.write_text(json.dumps({'tasks':[self.task]}))
            config = {'reminders_file':str(path)}
            self.assertEqual(enqueue_daily_reminders(self.state, config, self.now)['queued'], 1)
            self.assertEqual(enqueue_daily_reminders(self.state, config, self.now)['queued'], 0)
            self.assertEqual(enqueue_daily_reminders(self.state, config, self.now + timedelta(days=1))['queued'], 1)

    def test_initial_brief_does_not_consume_the_daily_eight_oclock_reminder(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'reminders.json'
            path.write_text(json.dumps({'tasks':[self.task]}))
            config = {'reminders_file':str(path)}
            early = self.now.replace(hour=2)
            self.assertEqual(enqueue_daily_reminders(self.state, config, early)['queued'], 0)
            self.assertEqual(enqueue_daily_reminders(self.state, config, early, initial=True)['queued'], 1)
            self.assertEqual(enqueue_daily_reminders(self.state, config, self.now)['queued'], 1)

    def test_due_date_is_reloaded_from_current_moodle_data(self):
        self.state.ingest(Result('moodle:AAIT', 'AAIT', 'ok', [Item('module-1:dates', 'Crafter',
            'Due: Monday, 16 November 2026, 11:59 PM', self.task['url'])]))
        _, events = reminder_events(self.state, {'tasks':[self.task]}, self.now)
        self.assertIn('16/11/2026 à 23:59', events[0][3]['body'])
        self.assertNotIn('15/11/2026', events[0][3]['body'])

    def test_changed_announcement_does_not_reuse_an_old_date(self):
        ref = {'source':'teams:SOS','key':'message-1','body_hash':body_hash('Presentation 16 November')}
        self.state.ingest(Result('teams:SOS', 'SOS', 'ok', [Item('message-1','Presentation','New schedule soon','https://teams.cloud.microsoft/')]))
        task = {**self.task,'date_mode':'published_date','date':'2026-11-16','reference':ref}
        _, events = reminder_events(self.state, {'tasks':[task]}, self.now)
        self.assertIn('DATE À CONFIRMER', events[0][3]['body'])
        self.assertNotIn('16/11/2026', events[0][3]['body'])

    def test_only_first_course_pings_and_calendar_uses_bucharest(self):
        other = {**self.task,'id':'other','course':'SOS'}
        moment = datetime(2026, 11, 11, 23, 30, tzinfo=timezone.utc)
        day, events = reminder_events(self.state, {'tasks':[self.task,other]}, moment)
        self.assertEqual(day, '2026-11-12')
        self.assertEqual(sum(event[3]['mention'] for event in events), 1)


if __name__ == '__main__':
    unittest.main()
