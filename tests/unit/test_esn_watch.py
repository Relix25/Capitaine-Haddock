import json
from pathlib import Path
import tempfile
import unittest

from courswatch.collectors.esn import EventState,ParseError,canonical_url,deliver,discover_role,notification,parse_page


def page(links,**extra):
    props={'username':'BucharestEvents','links':links,**extra}
    return '<script id="__NEXT_DATA__" type="application/json">'+json.dumps({'props':{'pageProps':props}})+'</script>'


def item(identity='1',url='https://forms.gle/event1',title='An event'):
    return {'id':identity,'url':url,'title':title,'description':'Details'}


class ParserTests(unittest.TestCase):
    def test_role_auto_detection_requires_one_mentionable_unmanaged_esn_role(self):
        role={'id':'123456789012345678','name':'ESN','mentionable':True,'managed':False}
        self.assertEqual(discover_role({},[role]),role['id'])
        self.assertIsNone(discover_role({},[]))
        self.assertIsNone(discover_role({},[{**role,'mentionable':False}]))
        self.assertIsNone(discover_role({},[role,{**role,'id':'987654321098765432'}]))
        self.assertEqual(discover_role({'role_id':'222222222222222222'},[]),'222222222222222222')

    def test_only_current_live_links_not_historical_seo_or_footer(self):
        raw=page([item()],account={'historical_resources':[item('old','https://forms.gle/old')]})
        raw+='<a href="https://linktr.ee/other">Suggested account</a>'
        self.assertEqual([x['url'] for x in parse_page(raw)],['https://forms.gle/event1'])

    def test_rejects_wrong_profile_and_error_pages(self):
        for raw in ('<html>Rate limited</html>',page([],username='other'),page([],hasPagePassword=True)):
            with self.assertRaises(ParseError): parse_page(raw)

    def test_normalizes_tracking_and_deduplicates_pinned_links(self):
        a=item(url='https://forms.gle/event1?utm_source=ig&fbclid=abc')
        self.assertEqual(len(parse_page(page([a],pinnedLinks=[item()]))),1)
        self.assertEqual(canonical_url(a['url']),'https://forms.gle/event1')

    def test_hidden_headers_gated_and_unsafe_urls_are_ignored(self):
        links=[{'id':'header','type':'HEADER'},item(url='javascript:alert(1)'),
               {**item('locked'),'locked':True}, {**item('gate'),'rules':{'gate':{'activeOrder':['password']}}},item()]
        self.assertEqual(len(parse_page(page(links))),1)

    def test_link_without_id_or_title_is_skipped_not_fatal(self):
        links=[item(),{'id':'','url':'https://forms.gle/noid','title':'Sans id'},
               {'id':'2','url':'https://forms.gle/notitle','title':'   '}]
        parsed=parse_page(page(links))
        self.assertEqual([x['url'] for x in parsed],['https://forms.gle/event1'])

    def test_profile_with_links_but_none_usable_is_rejected(self):
        with self.assertRaises(ParseError): parse_page(page([{'id':'header','type':'HEADER'}]))
        self.assertEqual(parse_page(page([])),[])


class StateTests(unittest.TestCase):
    def setUp(self): self.state=EventState(':memory:')
    def tearDown(self): self.state.db.close()

    def test_initial_snapshot_is_quiet_then_new_link_notified_once(self):
        self.assertTrue(self.state.ingest([item()],'2026-11-15T12:00:00Z')['initialized_now'])
        self.assertEqual(self.state.pending(),[])
        new=item('2','https://forms.gle/new')
        self.assertEqual(self.state.ingest([new,item()],'2026-11-15T12:05:00Z')['new_events'],1)
        self.assertEqual(self.state.ingest([item(),new],'2026-11-15T12:10:00Z')['new_events'],0)
        self.assertEqual(len(self.state.pending()),1)

    def test_rename_reorder_republication_and_id_changes_do_not_spam(self):
        self.state.ingest([item()],'a')
        self.state.ingest([],'b')
        self.assertEqual(self.state.ingest([item('newid',title='Renamed activity')],'c')['new_events'],0)
        self.assertEqual(self.state.pending(),[])

    def test_reused_id_with_new_destination_is_new(self):
        self.state.ingest([item()],'a')
        self.assertEqual(self.state.ingest([item(url='https://forms.gle/new')],'b')['new_events'],1)

    def test_outbox_survives_failure_and_role_not_ready(self):
        self.state.ingest([],'a'); self.state.ingest([item()],'b')
        self.assertEqual(deliver(self.state,{'channel_id':'123'},lambda *x:self.fail('must not send'))['delivery'],'waiting_role')
        config={'role_id':'123456789012345678','channel_id':'987654321098765432'}
        def fail(*args): raise RuntimeError('Network error')
        with self.assertRaises(RuntimeError): deliver(self.state,config,fail)
        self.assertEqual(len(self.state.pending()),1)
        sent=deliver(self.state,config,lambda *args:{'id':'555','channel_id':config['channel_id']})
        self.assertEqual(sent['confirmed'],1); self.assertEqual(self.state.pending(),[])

    def test_nonce_stays_stable_and_only_esn_can_be_pinged(self):
        self.state.ingest([],'a'); self.state.ingest([item(title='@everyone @here')],'b')
        event=self.state.pending()[0]
        payload=notification(event,'123456789012345678')
        self.assertEqual(payload['allowed_mentions'],{'parse':[],'roles':['123456789012345678'],'users':[]})
        self.assertNotIn('@everyone',payload['embeds'][0]['title'])
        self.assertTrue(payload['enforce_nonce'])
        self.assertEqual(payload['nonce'],notification(event,'123456789012345678')['nonce'])

    def test_state_persists_across_restart(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'events.db'
            first=EventState(path); first.ingest([item()],'a'); first.db.close()
            second=EventState(path)
            try: self.assertFalse(second.ingest([item()],'b')['initialized_now']); self.assertEqual(second.pending(),[])
            finally: second.db.close()


if __name__=='__main__': unittest.main()
