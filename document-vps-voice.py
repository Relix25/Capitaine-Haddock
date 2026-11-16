"""Guarded documentation of native voice samples and the user's 50% choice."""
from pathlib import Path
import hashlib
import json

proof = json.loads(Path('/tmp/cours-watch-bot-proof.json').read_text())
demo = json.loads(Path('/tmp/cours-watch-voice-demo-proof.json').read_text())
if not (proof.get('bot_ready') and proof.get('voice_enabled') and proof.get('voice_samples') == 5
        and proof.get('voice_probability') == 0.5 and demo.get('native_voice') and demo.get('waveform_present')):
    raise SystemExit('Verified voice assets, chosen probability and real Discord receipt required')
vault = Path('/srv/obsidian/vault/VPS')
service = vault / 'Services/Veille des cours UPB.md'
index = vault / 'Accueil VPS.md'
architecture = vault / 'Notes/VPS - architecture et exploitation.md'
expected = {
    service:'6b361834ac209e5c39e816407b053e6b7bfde315d786f8724bce44a4f99e6268',
    index:'bcb1d681ca510321af9b71a7bc733c469e4949957410bde289cbb43262a0c632',
    architecture:'72aedaad800bffbcf8be3408b72347b0e1ffad2afad7160ca184f67f842b9e4f',
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
    '- Cinquante-cinq tests couvrent les références, rappels, présentation Discord, santé, emojis, traductions et easter egg, dont la vraie méthode de réponse Discord et les erreurs sans texte privé.',
    "- Soixante-trois tests couvrent les références, rappels, présentation Discord, traductions et easter egg, dont les réponses vocales natives et le retour au texte en cas d'erreur.")
note = once(note,'\n## Connexion temporaire fermée après usage\n',
    Path('/tmp/cours-watch-voice-section.md').read_text() + '\n## Connexion temporaire fermée après usage\n')
new_index = once(original[index],'easter egg, détails privés et originaux,',
    'easter egg texte/vocal (50 %), détails privés et originaux,')
new_architecture = once(original[architecture],'Cinquante-cinq tests passent,','Soixante-trois tests passent,')
new_architecture += '''

Répliques vocales ajoutées le 12 novembre, selon le choix explicite de l'utilisateur : 50 % de chances d'un vocal à chaque déclenchement, sinon texte ; même délai de 30 secondes et exclusions. Cinq courts enregistrements existants de Haddock (2,8–10,1 s), sources publiques et SHA256 dans le manifest ; fichiers root sous `/opt/cours-watch/assets/haddock-audio`, sauvegardés dans le projet Cours. Pas de clonage ou synthèse de voix. Validation locale des chemins, tailles, empreintes, durée et forme d'onde ; pas de téléchargement au déclenchement. API multipart Discord native avec un seul OGG/Opus mono, flag IS_VOICE_MESSAGE, métadonnées mesurées, nonce et mentions interdites. Retour au texte sur erreur. Le vocal 1570255153528049674 a été accepté et identifié comme natif avec durée/forme d'onde, sans ping. Reçu `/etc/cours-watch/voice-demo.json`, config discord.easter_egg.voice_probability=0.5, état client prêt avec cinq samples. Les 63 tests passent. Permissions audio déjà présentes, aucun droit supplémentaire, nouveau port ou service. Préparation avec imageio-ffmpeg 0.8.1 dans le bot-venv ; installation ffmpeg système arrêtée pendant le téléchargement, sans changer les autres services.
'''
for path,text in ((service,note),(index,new_index),(architecture,new_architecture)):
    path.write_text(text)
print('Verified native voice samples and the chosen 50% mode documented in three Obsidian notes.')
