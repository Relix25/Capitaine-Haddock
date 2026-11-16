"""Record the verified stronger Haddock voice without altering the daily quota."""
import hashlib
import json
from pathlib import Path

proof = json.loads(Path('/tmp/cours-watch-bot-proof.json').read_text())
if not (proof.get('bot_ready') and proof.get('luna_persona') == 'haddock-punch-v2'
        and proof.get('luna_daily_token_limit') == 1_000_000):
    raise SystemExit('Active punch persona with unchanged quota required')
vault = Path('/srv/obsidian/vault/VPS')
expected = {'Services/Veille des cours UPB.md':'b439d1f7c403322946b54956012d50de411f18766e4dc9cd8155dabcd90ea3e4',
            'Accueil VPS.md':'3a7ba578cfbdfbb9eb89a82cf6ee0de9056bc36990c7634792da86ad9e1ee544',
            'Notes/VPS - architecture et exploitation.md':'12639cd24bb2058e012af780bdc7453b37675b04cc7fc97b18a16034ce282986'}
original = {name:(vault/name).read_bytes() for name in expected}
if any(hashlib.sha256(content).hexdigest() != expected[name] for name,content in original.items()):
    raise SystemExit('Notes changed since review; no write performed')
paragraph = '''
**Ton renforcé à la demande de l’utilisateur :** profil `haddock-punch-v2`, phrases courtes, reparties sèches, colères théâtrales et insultes burlesques visant aussi les camarades dans le chambrage. Le prompt privilégie une image liée au message et une chute plutôt qu’un préambule poli ; il varie les jurons et garde une vraie réponse pour les questions. Les nouveaux exemples sont originaux. Recherche fondée sur le [portrait officiel](https://www.tintin.com/fr/characters/le-capitaine-haddock) et le dossier [Haddock, une colère en stock](https://www.tintin.com/fr/news/6543/haddock-une-colere-en-stock), lus le 12 novembre. Trente tests ciblés repassés ; cinq appels réels fictifs ont éprouvé procrastination, pique contre le bot, plan absurde, aide sérieuse et échange banal. Le ton plus mordant a été constaté ; l’échange banal reste ignoré. Client redémarré et profil actif vérifié. Plafond de 1 M de tokens/jour, reset Bucarest et compteur persistant conservés ; les évaluations comptent dans ce quota. Le silence spontané et les règles de voix contextuelle restent les mêmes.
'''
notes = {name:content.decode('utf-8') for name,content in original.items()}
notes['Services/Veille des cours UPB.md'] = notes['Services/Veille des cours UPB.md'].replace(
    '### Conversations contextuelles avec Luna\n','### Conversations contextuelles avec Luna\n'+paragraph,1)
notes['Notes/VPS - architecture et exploitation.md'] += '\n\n### Ajustement du ton de Haddock\n'+paragraph
notes['Accueil VPS.md'] = notes['Accueil VPS.md'].replace('conversations Luna au ton Haddock,','conversations Luna au ton Haddock plus mordant et chambrage burlesque,',1)
if any((vault/name).read_bytes() != content for name,content in original.items()):
    raise SystemExit('Concurrent note change; no write performed')
for name,note in notes.items(): (vault/name).write_text(note,encoding='utf-8')
print(json.dumps({'obsidian_persona_updated':True,'notes':3,'persona':'haddock-punch-v2'}))
