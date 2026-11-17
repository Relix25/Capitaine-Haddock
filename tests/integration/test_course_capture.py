import json
from pathlib import Path
import tempfile
import unittest
from cours_watch import State, Result, Item
from course_capture import build_snapshot, publish_snapshot
from course_obsidian import validate_snapshot


class CaptureTests(unittest.TestCase):
    def test_fresh_forum_items_use_the_canonical_payload_stored_by_ingestion(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = State(root / 'state.db')
            try:
                item = Item('forum:p1', 'Synthetic forum', 'Known content',
                            'https://curs.upb.ro/2026/mod/forum/discuss.php?d=1&parent=2#p1')
                result = Result('moodle:AAIT', 'AAIT', 'ok', [item])
                state.ingest(result)
                snapshot = build_snapshot(root, {'courses': [{'id': 'AAIT', 'label': 'AAIT'}]},
                                          {'checked_utc': '2026-11-17T08:00:00+00:00'}, [result])
                course = snapshot['courses']['AAIT']
                self.assertEqual(course['fresh_items'], course['items'])
                self.assertNotIn('parent=', course['fresh_items'][0]['url'])
                self.assertEqual(course['fresh_items'][0]['url'],
                                 'https://curs.upb.ro/2026/mod/forum/discuss.php?d=1#p1')
                validate_snapshot(snapshot)
            finally:
                state.db.close()

    def test_disabled_export_creates_nothing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.assertEqual(publish_snapshot(root,{},{}),{'status':'disabled'})
            self.assertEqual(list(root.iterdir()),[])

    def test_dry_run_does_not_publish_even_when_enabled(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            config={'obsidian_export':{'enabled':True,'snapshot_file':str(root/'latest.json')}}
            self.assertEqual(publish_snapshot(root,config,{'dry_run':True}),{'status':'dry_run'})
            self.assertEqual(list(root.iterdir()),[])

    def test_snapshot_excludes_credentials_and_distinguishes_observed_items(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = State(root/'state.db')
            try:
                old = Item('old','Ancien message','Contenu ancien','https://teams.cloud.microsoft/')
                new = Item('new','Annonce','Nouveau rendu','https://teams.cloud.microsoft/')
                state.ingest(Result('teams:AAIT','AAIT','ok',[old]))
                state.ingest(Result('teams:AAIT','AAIT','ok',[new]))
                cfg = {'courses':[{'id':'AAIT','label':'AAIT'}],'discord':{'token':'SECRET_SENTINEL'},
                       'obsidian_export':{'enabled':True,'snapshot_file':str(root/'latest.json')}}
                report = {'checked_utc':'2026-11-12T14:00:00+00:00','sources':[]}
                result = publish_snapshot(root,cfg,report,[Result('teams:AAIT','AAIT','ok',[new])])
                self.assertEqual(result['status'],'ok')
                text = (root/'latest.json').read_text(encoding='utf-8')
                self.assertNotIn('SECRET_SENTINEL',text)
                data = json.loads(text)['courses']['AAIT']
                self.assertEqual(len(data['items']),2)
                self.assertEqual([item['key'] for item in data['fresh_items']],['new'])
                self.assertEqual(data['sources'][0]['status'],'ok')
            finally: state.db.close()


if __name__ == '__main__': unittest.main()
