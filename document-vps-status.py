"""Record the verified /status deployment with guarded Obsidian edits; no secrets."""
import hashlib
import json
from pathlib import Path

proof = json.loads(Path('/tmp/cours-watch-bot-proof.json').read_text())
if not proof.get('bot_ready') or 'status' not in proof.get('live_commands',[]):
    raise SystemExit('Live /status registration and readiness required')

vault = Path('/srv/obsidian/vault/VPS')
service = vault / 'Services/Veille des cours UPB.md'
index = vault / 'Accueil VPS.md'
architecture = vault / 'Notes/VPS - architecture et exploitation.md'
expected = {
    service:'c09fe7aa4ac0b90b69767aeb5ef8e225683591ea323b6f868173ff8d41a0a859',
    index:'95b8f1397b8eb0b975eca59b1e81f7cfb8b7594bcd7c3f22a7fcbbe6ac4402c8',
    architecture:'73370a4902262d23a5f4c5428c34592c956ee59ac68e4566647af7b2e953b48d',
}
original = {}
for path, checksum in expected.items():
    content = path.read_bytes()
    if hashlib.sha256(content).hexdigest() != checksum:
        raise SystemExit('Concurrent note change; stopped: ' + str(path))
    original[path] = content.decode('utf-8')

def replace_once(text, old, new):
    if text.count(old) != 1:
        raise SystemExit('Unexpected note content; no write performed')
    return text.replace(old, new, 1)

text = replace_once(original[service],
    '- Vingt-huit tests couvrent les références, rappels et la présentation Discord, y compris les droits du rôle, les réponses privées des boutons et la lecture du `.env` sans remplacer le webhook.',
    "- Trente-neuf tests couvrent les références, rappels, présentation Discord et santé du service, y compris les droits du rôle, les réponses privées, les confirmations d'envoi et les rappels pas encore dus.")
text = replace_once(text, 'est **active** pour `/cours`, `/actualiser` et « Plus d’infos ».',
                    'est **active** pour `/cours`, `/actualiser`, `/status` et « Plus d’infos ».')
section = Path('/tmp/cours-watch-status-section.md').read_text()
text = replace_once(text, '\n## Connexion temporaire fermée après usage\n', section + '\n## Connexion temporaire fermée après usage\n')
new_index = replace_once(original[index], 'bot actif : embeds, détails privés, /cours et /actualiser ;',
    'bot actif : embeds, détails privés, /cours, /actualiser et /status (santé, prochains envois et confirmations) ;')
new_architecture = replace_once(original[architecture], 'Vingt-huit tests passent. Administrativ est exclu.',
    'Trente-neuf tests passent, dont la santé, les échéances de diffusion et les confirmations. Administrativ est exclu.')
new_architecture += '''

Commande `/status` ajoutée et retrouvée dans l'API Discord le 12 novembre : réponse privée, même serveur/salon/rôle UPB. Le module `course_health.py` lit les rapports et SQLite, interroge systemd sans privilège et présente connexion/latence du bot, huit sources, dernière collecte, maintien Moodle, prochain passage effectif, rappel du jour et trois derniers lots avec liens et confirmations. Un rappel avant 08:00 n'est pas annoncé manquant ; après 08:20, un envoi attendu sans confirmation est signalé, sauf collecte en cours. Les timers désactivés, sources en échec, collectes anciennes et messages en attente restent visibles. « Confirmé » signifie accepté par Discord, sans suivi de lecture. Aucun ping ni collecte déclenché par cette commande. Les résultats manuels et les reprises Gateway sont enregistrés. À 02:50, les huit sources étaient OK au dernier contrôle, la file vide et le dernier point confirmé 4/4 ; prochain passage prévu à 08:00. Les 39 tests passent dans l'environnement du bot ; aperçu réel lu sans publication avec `preview_status.py`.
'''

for path, content in ((service,text),(index,new_index),(architecture,new_architecture)):
    path.write_text(content)
print('Three Obsidian notes updated for verified /status; unrelated text and secrets untouched.')
