"""Short Haddock exclamations, keyword matching and a channel-wide cooldown."""
import random
import re
import time
import unicodedata

# Short familiar exclamations, rather than invented quotations attributed to Hergé.
QUOTES = (
    'Mille sabords !',
    'Tonnerre de Brest !',
    'Bachi-bouzouk !',
    'Moule à gaufres !',
    'Marin d’eau douce !',
    'Bougre d’ectoplasme !',
    'Sapajou !',
    'Espèce de protozoaire !',
)
TERMS = re.compile(r'\b(?:haddock|archibald|tintin|milou|tournesol|moulinsart|'
                   r'mille(?: millions de mille)? sabords|tonnerre de brest|'
                   r'bachi\s*bouzouks?|moules? a gaufres?|ectoplasmes?|'
                   r'marins? d eau douce|loch lomond|rackham le rouge)\b')


def triggered(content,bot_mentioned=False):
    text = unicodedata.normalize('NFKD',str(content or '')[:5000].casefold())
    text = ''.join(char for char in text if not unicodedata.combining(char))
    text = re.sub(r'[^a-z0-9]+',' ',text).strip()
    return bool(bot_mentioned or TERMS.search(text))


class HaddockEasterEgg:
    def __init__(self,cooldown_seconds=30,chooser=random.choice,clock=time.monotonic):
        self.cooldown = max(1,float(cooldown_seconds))
        self.chooser = chooser
        self.clock = clock
        self.last_time = None
        self.last_quote = None

    def choose(self):
        now = self.clock()
        if self.last_time is not None and now - self.last_time < self.cooldown:
            return None
        quote = self.chooser(tuple(quote for quote in QUOTES if quote != self.last_quote))
        self.last_time,self.last_quote = now,quote
        return quote
