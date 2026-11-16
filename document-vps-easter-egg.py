"""Document the expressly authorized, verified Haddock keyword feature."""
from pathlib import Path
import hashlib
import json

proof = json.loads(Path('/tmp/cours-watch-bot-proof.json').read_text())
if not proof.get('bot_ready') or not proof.get('easter_egg_enabled') or not proof.get('message_content_intent'):
    raise SystemExit('Verified active easter egg and approved Message Content access required')
vault = Path('/srv/obsidian/vault/VPS')
service = vault / 'Services/Veille des cours UPB.md'
index = vault / 'Accueil VPS.md'
architecture = vault / 'Notes/VPS - architecture et exploitation.md'
expected = {
    service:'45d6efcd4e943a7928fbe568d65027edc56ed1719213395ab19e672b64bc45a4',
    index:'7e406d3b80b4397c3263fb01f13a39e443ad1bccb8cda9b9362cd592ae2e0ae6',
    architecture:'e092e48ab9002e8ee085d2192d106f5cce2915a8826982319d80dc47ff3475d3',
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

note = once(original[service],
    '- Quarante-sept tests couvrent les références, rappels, présentation Discord, santé, emojis et traductions, y compris les droits du rôle, les réponses privées et la conservation des originaux.',
    '- Cinquante-trois tests couvrent les références, rappels, présentation Discord, santé, emojis, traductions et easter egg, y compris les droits du rôle, les réponses privées, les mots-clés et le délai entre réponses.')
note = once(note,
    "Uniquement l'intent Guilds ; pas d'accès au contenu des messages ou à la liste des membres via les intents privilégiés. Les commandes sont limitées au serveur configuré et les contrôles exigent le salon et le rôle UPB.",
    "Guilds, Guild Messages et Message Content sont activés pour l'easter egg après autorisation explicite de l'utilisateur ; Members et Presence restent désactivés. Les commandes et réponses de l'easter egg sont limitées au serveur, salon et rôle UPB configurés.")
note = once(note,'\n## Connexion temporaire fermée après usage\n',
    Path('/tmp/cours-watch-easter-egg-section.md').read_text() + '\n## Connexion temporaire fermée après usage\n')
new_index = once(original[index],'embeds avec emojis Haddock, détails privés et originaux,',
    'embeds avec emojis Haddock, easter egg, détails privés et originaux,')
new_architecture = once(original[architecture],
    'Quarante-sept tests passent, dont la santé, les échéances de diffusion, les confirmations, les emojis et les traductions avec originaux conservés.',
    "Cinquante-trois tests passent, dont la santé, les confirmations, les traductions avec originaux conservés et l'easter egg avec délai et exclusions.")
new_architecture = once(new_architecture,"Pas d'intents privilégiés Message Content, Members ou Presence.",
    "Message Content est désormais activé avec Guild Messages pour l'easter egg expressément autorisé le 12 novembre ; Members et Presence restent désactivés.")
new_architecture += '''

Easter egg Haddock activé le 12 novembre après autorisation explicite de l'utilisateur du Message Content Intent et de son périmètre. Détection des noms et termes associés dans les nouveaux messages du salon privé, rôle UPB exigé ; huit exclamations courtes aléatoires, sans répétition immédiate et avec 30 secondes minimum entre réponses. Bots, webhooks, DM et autres salons ignorés, aucun ping, aucune collecte. Discord fournit les contenus des salons visibles par le bot ; le code filtre le serveur/salon/rôle. Cache de messages discord.py désactivé (`max_messages=None`), pas de texte ni d'auteur enregistré ; seules heure et référence de réponse peuvent figurer dans bot-status.json. Intents actifs : Guilds, Guild Messages et Message Content ; Members et Presence inactifs. Paramètres discord.easter_egg.enabled/cooldown_seconds, fonction désactivée si absents. Les 53 tests passent et l'API/Gateway confirment l'accès et le client prêt ; cette vérification ne remplace pas un essai humain du déclencheur. Pas de nouveau service, port ou fournisseur externe.
'''
for path,text in ((service,note),(index,new_index),(architecture,new_architecture)):
    path.write_text(text)
print('Authorized Haddock easter egg and Message Content scope documented in three Obsidian notes.')
