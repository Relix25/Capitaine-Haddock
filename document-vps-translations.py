"""Guarded Obsidian update for the verified local French translation pipeline."""
from pathlib import Path
import hashlib
import json

proof = json.loads(Path('/tmp/cours-watch-translations-proof.json').read_text())
if len(proof.get('edited',[])) != 3:
    raise SystemExit('Three verified historical edits required')
vault = Path('/srv/obsidian/vault/VPS')
service = vault / 'Services/Veille des cours UPB.md'
index = vault / 'Accueil VPS.md'
architecture = vault / 'Notes/VPS - architecture et exploitation.md'
expected = {
    service:'ee69705e1b2301cfcef31b4c066afcd7e13d2bc940054cedde55b848a3d39c6e',
    index:'2796967b90edb89cc335719db40c6243d370274c5c147cd4857edd25889ff596',
    architecture:'cb4e010823dd1749962b983973383e4da714c8c7cc91f9fa5649bb03c82d234f',
}
original = {}
for path,checksum in expected.items():
    blob = path.read_bytes()
    if hashlib.sha256(blob).hexdigest() != checksum:
        raise SystemExit('Concurrent note change; stopped: ' + str(path))
    original[path] = blob.decode('utf-8')

def once(text,old,new):
    if text.count(old) != 1:
        raise SystemExit('Unexpected note layout; no write performed')
    return text.replace(old,new,1)

section = Path('/tmp/cours-watch-translation-section.md').read_text()
note = once(original[service],
    "- Quarante-deux tests couvrent les références, rappels, présentation Discord, santé du service et emojis personnalisés, y compris les droits du rôle, les réponses privées, les confirmations d'envoi et les rappels pas encore dus.",
    '- Quarante-sept tests couvrent les références, rappels, présentation Discord, santé, emojis et traductions, y compris les droits du rôle, les réponses privées et la conservation des originaux.')
note = once(note,'\n## Connexion temporaire fermée après usage\n',section + '\n## Connexion temporaire fermée après usage\n')
new_index = once(original[index],'bot actif : embeds avec emojis Haddock, détails privés,',
    'bot actif : annonces traduites en français, embeds avec emojis Haddock, détails privés et originaux,')
new_architecture = once(original[architecture],
    'Quarante-deux tests passent, dont la santé, les échéances de diffusion, les confirmations et les emojis personnalisés.',
    'Quarante-sept tests passent, dont la santé, les échéances de diffusion, les confirmations, les emojis et les traductions avec originaux conservés.')
new_architecture += '''

Traduction locale des messages ajoutée le 12 novembre : CTranslate2 4.9.0 sur CPU, modèles Argos 1.9 anglais→français et roumain→anglais→français, environnement séparé `/opt/cours-watch/translation-venv`, modèles et code root sous `/opt/cours-watch`. Activation `/etc/cours-watch/translation.json`, cache privé indépendant `/var/lib/cours-watch/translations.db`. Les nouveaux messages Teams et de forum Moodle sont traduits avant notification ; les détails privés gardent l'original. Les contenus source, leurs empreintes et outbox restent originaux. Les collecteurs n'attribuent pas automatiquement une réponse à un enseignant. URL, heures, nombres, commandes et fichiers sont protégés ; un échec laisse l'original explicitement identifié et sera réessayé. Pas de clé d'API, d'envoi vers un fournisseur de traduction, de GPU ni de nouveau port/service permanent. Le processus est lancé à la demande et libère sa mémoire ensuite. Limite linguistique : anglais, roumain et français ; l'original fait référence.

Validation réelle : 28 messages déjà recueillis traduits, six traductions importantes relues, quatre archives par cours acceptées sans ping, puis zéro doublon ; trois anciennes notifications actualisées avec propriété vérifiée. Archives datées, originaux dans les détails, langues et caractère automatique identifiés. Les 47 tests passent. Le bilan est `reports/translation.json` et le relevé privé détaillé `reports/translations-backfill.json`. Les fichiers, annonces et sites Moodle/Teams restent en lecture seule.
'''
for path,text in ((service,note),(index,new_index),(architecture,new_architecture)):
    path.write_text(text)
print('Local French translations documented in three Obsidian notes; no secrets copied.')
