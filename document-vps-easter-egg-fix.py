"""Guarded documentation of the reproduced reply error and verified real response."""
from pathlib import Path
import hashlib
import json

proof = json.loads(Path('/tmp/cours-watch-bot-proof.json').read_text())
if not proof.get('bot_ready') or proof.get('last_easter_egg',{}).get('reply_id') != '1570252440081530974':
    raise SystemExit('Ready client and verified real reply required')
root = Path('/srv/obsidian/vault/VPS')
service = root / 'Services/Veille des cours UPB.md'
architecture = root / 'Notes/VPS - architecture et exploitation.md'
expected = {service:'e4feda4d2512df3b4af477efe7cee45405ef21cff4477a2a5535cdd244f7a877',
            architecture:'728c9b3f9f7b20e83eb305b801b8cf6082b0d7dcf3b104d849b63f2a5841299a'}
texts = {}
for path,checksum in expected.items():
    blob = path.read_bytes()
    if hashlib.sha256(blob).hexdigest() != checksum:
        raise SystemExit('Concurrent note change; stopped: ' + str(path))
    texts[path] = blob.decode('utf-8')

def once(text,old,new):
    if text.count(old) != 1:
        raise SystemExit('Unexpected note layout; no write performed')
    return text.replace(old,new,1)

note = once(texts[service],
    '- Cinquante-trois tests couvrent les références, rappels, présentation Discord, santé, emojis, traductions et easter egg, y compris les droits du rôle, les réponses privées, les mots-clés et le délai entre réponses.',
    '- Cinquante-cinq tests couvrent les références, rappels, présentation Discord, santé, emojis, traductions et easter egg, dont la vraie méthode de réponse Discord et les erreurs sans texte privé.')
note = once(note,
    "Les 53 tests passent, notamment délai, exclusions et absence de ping ; un essai humain n'est pas présumé par ce contrôle.",
    "Le paramètre incompatible fail_if_not_exists a été retiré de Message.reply. Les 55 tests passent, dont la vraie méthode de réponse ; à 03:42, une reprise ciblée du vrai message de test a produit une [réponse confirmée](https://discord.com/channels/1376984512046563519/1570209088929800282/1570252440081530974), puis l'écoute normale a repris. Les erreurs de callback sont désormais visibles uniquement par leur type, sans texte privé.")
detail = once(texts[architecture],'Cinquante-trois tests passent,','Cinquante-cinq tests passent,')
detail += '''

Correctif de l'easter egg le 12 novembre : le premier test antérieur à l'activation ne pouvait pas déclencher la fonction ; un second test a révélé un vrai défaut d'envoi. Message.reply transmettait un paramètre fail_if_not_exists non accepté par Messageable.send, provoquant un TypeError masqué par le logger inactif. Paramètre retiré ; on_error rend maintenant visibles uniquement le callback et le type d'erreur, sans arguments ni texte d'exception. Un test de régression avec la vraie méthode Discord reproduit l'échec avant correction ; les 55 tests passent ensuite. Réponse réelle confirmée le 12 novembre à 03:42 à partir d'une reprise ciblée du message de test de l'utilisateur : https://discord.com/channels/1376984512046563519/1570209088929800282/1570252440081530974 . Écoute normale redémarrée et prête, autorisation Message Content et délai de 30 secondes conservés. Aucune nouvelle permission ni ouverture réseau.
'''
service.write_text(note)
architecture.write_text(detail)
print('Reproduced reply error, privacy-safe diagnostics and verified real response documented in Obsidian.')
