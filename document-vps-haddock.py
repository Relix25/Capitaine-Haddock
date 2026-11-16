"""Guarded documentation of verified guild emojis and existing-card updates."""
import hashlib
import json
from pathlib import Path

proof = json.loads(Path('/tmp/cours-watch-haddock-proof.json').read_text())
if len(proof.get('guild_emojis',[])) != 4 or len(proof.get('updated_cards',[])) != 4:
    raise SystemExit('Verified four guild emojis and four updated cards required')
vault = Path('/srv/obsidian/vault/VPS')
service = vault / 'Services/Veille des cours UPB.md'
index = vault / 'Accueil VPS.md'
architecture = vault / 'Notes/VPS - architecture et exploitation.md'
expected = {
    service:'267cf4f782c50b18ddc42c8312f41e7db0aadb0699a0fe57e16c71cffe266d91',
    index:'970958dd397e5acfd33ec28f9f7cad40c176ff5989755b99678ff18e08f84fe2',
    architecture:'f7b1beb21383e90f89594c5068282fb5aa00fad5c3cc0039bf30fc28c0134037',
}
original = {}
for path, checksum in expected.items():
    blob = path.read_bytes()
    if hashlib.sha256(blob).hexdigest() != checksum:
        raise SystemExit('Concurrent note change; stopped: ' + str(path))
    original[path] = blob.decode('utf-8')

def once(text,old,new):
    if text.count(old) != 1:
        raise SystemExit('Unexpected note layout; no write performed')
    return text.replace(old,new,1)

note = once(original[service],
    "- Trente-neuf tests couvrent les références, rappels, présentation Discord et santé du service, y compris les droits du rôle, les réponses privées, les confirmations d'envoi et les rappels pas encore dus.",
    "- Quarante-deux tests couvrent les références, rappels, présentation Discord, santé du service et emojis personnalisés, y compris les droits du rôle, les réponses privées, les confirmations d'envoi et les rappels pas encore dus.")
note = once(note,
    'Les quatre messages initiaux ont été transformés en **embeds compacts**, sans nouveau ping : cours, dates et urgence, avec liens de source. Les rappels suivants utilisent cette présentation.',
    'Les quatre cartes interactives utilisent des **embeds compacts** : cours, dates et urgence, avec détails privés. Les rappels suivants utilisent cette présentation. Les quatre anciens messages sans embed sont conservés.')
note = once(note,'\n## Connexion temporaire fermée après usage\n',
            Path('/tmp/cours-watch-haddock-section.md').read_text() + '\n## Connexion temporaire fermée après usage\n')
new_index = once(original[index],'bot actif : embeds, détails privés,',
    'bot actif : embeds avec emojis Haddock, détails privés,')
new_architecture = once(original[architecture],
    'Trente-neuf tests passent, dont la santé, les échéances de diffusion et les confirmations.',
    'Quarante-deux tests passent, dont la santé, les échéances de diffusion, les confirmations et les emojis personnalisés.')
new_architecture += '''

Pack Haddock fourni par l'utilisateur ajouté au serveur Discord le 12 novembre : quatre emojis de serveur, sans restriction de rôle ajoutée ni changement des permissions. Longue-vue pour projets/détails, ampoule pour les oraux, joie pour les confirmations et « Mille sabords » pour urgences/erreurs. Identifiants et empreintes dans `/etc/cours-watch/emojis.json`, sans secret ; validation et références par `haddock_emojis.py`. Les quatre cartes interactives existantes ont été modifiées par API en conservant contenu et boutons, sans nouveau message ni ping. Les quatre anciens messages sans embed sont préservés. Les futures cartes et les trois commandes utilisent la même présentation ; textes IMPORTANT/URGENT et couleurs conservés. Références dans les descriptions/valeurs de champs et boutons, titres en texte, vignette avec l'image. Les 42 tests passent et le bot est actif et prêt ; les trois commandes sont retrouvées dans l'API Discord.
'''
for path,text in ((service,note),(index,new_index),(architecture,new_architecture)):
    path.write_text(text)
print('Haddock guild emojis and verified cards documented in three Obsidian notes; no secrets copied.')
