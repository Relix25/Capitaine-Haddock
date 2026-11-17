import unittest
from unittest.mock import patch

from courswatch.discord_app.emojis import icon, button_emoji, image_url
from courswatch.discord_app.presentation import compact_embed, embed_payload
from courswatch.discord_app.health import status_embed


CATALOG = {kind:{'id':str(1570240437279662120 + n),'name':name} for n,(kind,name) in enumerate([
    ('success','haddock_joie_depot'),('project','haddock_longue_vue_projet'),
    ('oral','haddock_idee_oral'),('warning','haddock_mille_sabords')])}


class HaddockEmojiTests(unittest.TestCase):
    def test_catalog_references_validate_ids_and_names(self):
        with patch('courswatch.discord_app.emojis.catalog',return_value={'warning':{'id':'bad','name':'@everyone'}}):
            self.assertEqual(icon('warning','⚠️'),'⚠️')
            self.assertEqual(button_emoji(),{'name':'📖'})
            self.assertIsNone(image_url('warning'))

    def test_custom_icons_are_in_supported_embed_values_and_buttons(self):
        body = ('🟠 **IMPORTANT** — **Crafter** : **15/11/2026 à 23:59** (J-3).\n'
                '📌 À prévoir — **Oral** : **16/11/2026** (J-4).\n'
                '🔴 **URGENT** — **Dépôt** : **12/11/2026** (aujourd’hui).\n'
                '⚠️ **DATE À CONFIRMER — Autre** : source modifiée.')
        with patch('courswatch.discord_app.emojis.catalog',return_value=CATALOG):
            payload = embed_payload({'kind':'daily_reminder','course':'SOS','body':body},interactive=True)
            card = payload['embeds'][0]
            self.assertIn('haddock_longue_vue_projet',card['fields'][0]['value'])
            self.assertIn('haddock_idee_oral',card['fields'][1]['value'])
            self.assertIn('haddock_mille_sabords',card['fields'][2]['value'])
            self.assertIn('haddock_mille_sabords',card['fields'][3]['value'])
            self.assertTrue(all('<:' not in field['name'] for field in card['fields']))
            self.assertEqual(payload['components'][0]['components'][0]['emoji'],button_emoji())
            self.assertIn('cdn.discordapp.com',card['thumbnail']['url'])
            self.assertIn('IMPORTANT',card['fields'][0]['value'])
            self.assertIn('URGENT',card['fields'][2]['value'])

    def test_source_recovery_and_health_use_success_emoji(self):
        with patch('courswatch.discord_app.emojis.catalog',return_value=CATALOG):
            card = compact_embed({'kind':'source_recovered','course':'SOS','body':'Accès rétabli'})
            self.assertIn('haddock_joie_depot',card['description'])
            snapshot = {'issues':[],'bot_ready':True,'latency_ms':30,
                'sources':{'moodle':{'ok':4,'total':4},'teams':{'ok':4,'total':4}},
                'last_collection_utc':None,'session':{},'running':False,'automatic':True,
                'next_check_utc':None,'today':{'state':'confirmed','confirmed':4,'total':4,'hour':8},
                'batches':[],'pending':0,'last_delivery_utc':None}
            health = status_embed(snapshot)
            self.assertIn('haddock_joie_depot',health['description'])
            self.assertNotIn('<:',health['title'])
            self.assertTrue(any('4/4 messages confirmés' in field['value'] for field in health['fields']))
