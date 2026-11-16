from datetime import datetime
import json
from pathlib import Path
import tempfile
import unittest
from zoneinfo import ZoneInfo

from cours_watch import State, Item, Result
from course_reminders import body_hash, reminder_events
from course_tasks import task_rows, task_pages


class TaskTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.state = State(self.root / 'state.db')
        self.now = datetime(2026,11,17,8,tzinfo=ZoneInfo('Europe/Bucharest'))
        self.tasks = []

    def tearDown(self):
        self.state.db.close()
        self.directory.cleanup()

    def add(self, ident, due, **extra):
        task = {'id':ident,'course':'SOS','title':ident,'action':'Préparer les diapositives.',
                'date_mode':'moodle_due','url':'https://curs.upb.ro/2026/mod/assign/view.php?id=' + ident,
                'reference':{'source':'moodle:SOS','key':ident}, **extra}
        self.tasks.append(task)
        self.state.ingest(Result(task['reference']['source'],task['course'],'ok',
            [Item(task['reference']['key'],ident,'Due: ' + due,task['url'])]))
        return task

    def load(self, course='tous', *, include_past=False):
        path = self.root / 'reminders.json'
        path.write_text(json.dumps({'tasks':self.tasks}),encoding='utf-8')
        return task_rows(self.root,{'reminders_file':str(path)},course,self.now,include_past=include_past)[0]

    def test_nearest_upcoming_deadline_first_and_optional_past_dates_last(self):
        self.add('old','15 November 2026, 11:59 PM')
        self.add('new','21 January 2027, 11:59 PM',course='Big Data')
        self.add('middle','3 December 2026, 11:59 PM')
        rows = self.load()
        self.assertEqual([r['task']['id'] for r in rows],['middle','new'])
        archived = self.load(include_past=True)
        self.assertEqual([r['task']['id'] for r in archived],['middle','new','old'])
        pages = task_pages(archived,self.now)
        value = pages[-1]['embed']['description']
        self.assertIn('rendu personnel à vérifier',value)
        self.assertNotIn('non rendu',value)
        # The daily reminder policy continues to exclude elapsed deadlines.
        _, events = reminder_events(self.state,{'tasks':self.tasks},self.now)
        self.assertNotIn('**old**','\n'.join(event[3]['body'] for event in events))

    def test_current_moodle_date_wins_over_previously_observed_date(self):
        task = self.add('task','15 November 2026, 11:59 PM')
        self.state.ingest(Result('moodle:SOS','SOS','ok',
            [Item('task','task','Due: 19 November 2026, 11:59 PM',task['url'])]))
        self.assertEqual(self.load()[0]['due'].day,19)

    def test_changed_announcement_has_no_unverified_date_and_sorts_last(self):
        self.add('known','20 November 2026, 11:59 PM')
        self.add('changed','20 November 2026, 11:59 PM',date_mode='published_date',date='2026-11-19',
            reference={'source':'moodle:SOS','key':'changed','body_hash':body_hash('Original announcement')})
        rows = self.load()
        self.assertEqual(rows[-1]['task']['id'],'changed')
        self.assertTrue(rows[-1]['unconfirmed'])
        self.assertNotIn('19/11/2026',str(task_pages(rows,self.now)))

    def test_date_only_and_midnight_remain_distinct(self):
        self.add('midnight','17 November 2026, 12:00 AM')
        self.add('oral','18 November 2026, 12:00 AM',date_mode='published_date',date='2026-11-18')
        text = str(task_pages(self.load(include_past=True),self.now))
        self.assertIn('heure non publiée',text.casefold())
        self.assertIn('début de journée',text)

    def test_filter_and_explicit_completed_flag(self):
        self.add('done','18 November 2026, 11:59 PM',completed=True)
        self.add('sos','18 November 2026, 11:59 PM')
        self.add('other','18 November 2026, 11:59 PM',course='AAIT')
        self.assertEqual([r['task']['id'] for r in self.load('SOS')],['sos'])

    def test_stale_sources_are_labelled_and_read_does_not_change_state(self):
        self.add('task','18 November 2026, 11:59 PM')
        self.state.ingest(Result('moodle:SOS','SOS','auth_required',[]))
        before = list(self.state.db.iterdump())
        self.assertIn('Dernière date connue',str(task_pages(self.load(),self.now)))
        self.assertEqual(before,list(self.state.db.iterdump()))

    def test_pagination_retains_every_task_and_escapes_mentions(self):
        for index in range(12):
            self.add(str(index),'18 November 2026, 11:59 PM',title='@everyone **test**')
        pages = task_pages(self.load(),self.now)
        self.assertEqual(len(pages),12)
        self.assertTrue(all(len(page['embed']['fields']) == 1 for page in pages))
        self.assertNotIn('@everyone',str(pages))
        self.assertTrue(all(len(str(page)) < 6000 for page in pages))
        self.assertTrue(all(page['links'][0]['label'] == 'Ouvrir les consignes' for page in pages))

    def test_past_dates_closest_to_today_first_after_upcoming_dates(self):
        self.add('recent','16 November 2026, 11:59 PM')
        self.add('older','15 November 2026, 11:59 PM')
        self.add('upcoming','18 November 2026, 11:59 PM')
        self.assertEqual([r['task']['id'] for r in self.load(include_past=True)],['upcoming','recent','older'])


if __name__ == '__main__':
    unittest.main()
