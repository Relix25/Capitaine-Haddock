import unittest
from courswatch.conversation.easter_egg import HaddockEasterEgg, QUOTES, triggered


class EasterEggTests(unittest.TestCase):
    def test_keywords_ignore_case_accents_and_punctuation(self):
        for text in ('Capitaine HADDOCK !','Tintin et Milou','Moulinsart','MILLE SABORDS !',
                     'Bachi-bouzouks !','Moule à gaufres','marin d’eau douce','Loch Lomond'):
            with self.subTest(text=text):
                self.assertTrue(triggered(text))
        self.assertTrue(triggered('',bot_mentioned=True))

    def test_unrelated_words_and_substrings_do_not_trigger(self):
        for text in ('Rendu demain à 15 h','capitaine du projet','haddockophile','tintinesque','Brest'):
            with self.subTest(text=text):
                self.assertFalse(triggered(text))

    def test_cooldown_and_no_immediate_repeat(self):
        current = [0]
        egg = HaddockEasterEgg(30,chooser=lambda values:values[0],clock=lambda:current[0])
        first = egg.choose()
        self.assertIn(first,QUOTES)
        current[0] = 29
        self.assertIsNone(egg.choose())
        current[0] = 30
        second = egg.choose()
        self.assertIn(second,QUOTES)
        self.assertNotEqual(first,second)
