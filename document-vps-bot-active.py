"""Record only verified live interactive activation, preserving unrelated Obsidian notes."""
from pathlib import Path
import hashlib
import json

proof = json.loads(Path('/tmp/cours-watch-bot-proof.json').read_text())
if proof['delivery_mode'] != 'bot_channel' or not proof['bot_ready'] or not proof.get('last_private_detail'):
    raise SystemExit('Live bot and private click verification required')
vault = Path('/srv/obsidian/vault/VPS')
service = vault / 'Services/Veille des cours UPB.md'
index = vault / 'Accueil VPS.md'
architecture = vault / 'Notes/VPS - architecture et exploitation.md'
expected = {service:'3522053d2e7ee2e6d4652b39c4c170c80f4330c6a21bfb6cb6697420115ad423',
            index:'73f2171f7b255015063540e5b310c4da5a9e263f8728c4bcb19eb89fdf6bad23',
            architecture:'b854ea8c8db240e5084f9189f8b753fbbc5ebe89ff2bfee4080f5c0fc0cf2fb7'}
for path, checksum in expected.items():
    if hashlib.sha256(path.read_bytes()).hexdigest() != checksum:
        raise SystemExit('Concurrent note change; stopped: ' + str(path))
text = index.read_text()
old = '- [[Veille des cours UPB]] — embeds compacts, rappels à 08:00 et urgence J-3/J-1 ; application Discord préparée pour commandes et détails privés, jeton à valider ; accès de connexion fermé.'
if text.count(old) != 1:
    raise SystemExit('Unexpected index layout')
new_index = text.replace(old, '- [[Veille des cours UPB]] — bot actif : embeds, détails privés, /cours et /actualiser ; rappels à 08:00, IMPORTANT J-3 / URGENT J-1 ; accès de connexion fermé.')
text = architecture.read_text()
marker = '\n## Veille des cours UPB\n'
if text.count(marker) != 1:
    raise SystemExit('Unexpected architecture layout')
prefix, section = text.split(marker)
old = "activation en attente d'un Bot Token validé. Le webhook reste le transport opérationnel pendant cette étape."
new = "application authentifiée et client actif avec mode bot_channel, presentation embed et interactions actives. Le webhook initial reste conservé comme configuration de secours."
if section.count(old) != 1:
    raise SystemExit('Unexpected pending activation description')
section = section.replace(old,new,1)
section += '''

Activation réelle du 12 novembre : `cours-watch-bot.service` actif et activé au démarrage (WantedBy multi-user.target). Les commandes `/cours` et `/actualiser` ont été retrouvées dans l'API Discord ; les quatre cours ont une carte interactive, publiée sans ping supplémentaire. Un clic réel « Plus d’infos » a déclenché une réponse privée, puis l'utilisateur a confirmé les détails et commandes. Les rappels de 08:00 utilisent ce même transport et gardent un seul ping UPB par lot. Pas de port public ouvert, ni de modification des documents Moodle/Teams.
'''
note = Path('/tmp/cours-watch-service-note.md').read_text()
if '**Test réel effectué et confirmé' not in note or '**active**' not in note:
    raise SystemExit('Unexpected service note')
note += '\n\nRéponse privée réelle confirmée à `' + proof['last_private_detail'] + '` (UTC) ; vérification des commandes et composants via l’API Discord. Aucun jeton dans cette preuve.\n'
service.write_text(note)
index.write_text(new_index)
architecture.write_text(prefix + marker + section)
print('Live interactive bot, commands and private details documented in three Obsidian notes; no secrets copied.')
