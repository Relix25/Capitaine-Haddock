import unittest
from discord_presentation import compact_embed, embed_payload, private_details, interaction_allowed, DETAIL_CUSTOM_ID


class PresentationTests(unittest.TestCase):
    def event(self):
        return {'kind':'daily_reminder','course':'AAIT','title':'Rappels','url':'',
                'body':'🟠 **IMPORTANT** — **Crafter** : **15/11/2026 à 23:59** (J-3).\n'
                       'Texte détaillé à garder privé.\n[Source](<https://curs.upb.ro/2026/mod/assign/view.php?id=35700>)'}

    def test_compact_card_keeps_date_and_hides_long_instructions(self):
        card = compact_embed(self.event(), interactive=True)
        self.assertIn('15/11/2026',card['fields'][0]['value'])
        self.assertIn('IMPORTANT',card['fields'][0]['value'])
        self.assertNotIn('Texte détaillé',str(card))
        self.assertIn('Texte détaillé',private_details(self.event())[0])

    def test_webhook_has_sources_but_no_fake_interactive_button(self):
        payload = embed_payload(self.event())
        self.assertNotIn('components',payload)
        self.assertIn('https://curs.upb.ro',str(payload['embeds']))
        interactive = embed_payload(self.event(),interactive=True)
        self.assertEqual(interactive['components'][0]['components'][0]['custom_id'],DETAIL_CUSTOM_ID)

    def test_private_details_never_ping_or_truncate_the_snapshot(self):
        event = self.event()
        event['body'] = '@everyone\n' + 'x' * 4500
        pages = private_details(event)
        self.assertEqual(sum(page.count('x') for page in pages),4500)
        self.assertTrue(all(len(page) <= 1950 for page in pages))
        self.assertNotIn('@everyone',''.join(pages))

    def test_interaction_access_requires_guild_channel_and_upb_role(self):
        config = {'guild_id':'1','channel_id':'2','role_ids':['3']}
        self.assertTrue(interaction_allowed(1,2,[3],config))
        self.assertFalse(interaction_allowed(9,2,[3],config))
        self.assertFalse(interaction_allowed(1,9,[3],config))
        self.assertFalse(interaction_allowed(1,2,[9],config))


if __name__ == '__main__':
    unittest.main()
