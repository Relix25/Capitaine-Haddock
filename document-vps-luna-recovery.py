"""Document message-level failure handling after the verified invalid_response incident."""
import hashlib
import json
from pathlib import Path

proof = json.loads(Path('/tmp/cours-watch-bot-proof.json').read_text())
if not proof.get('bot_ready') or proof.get('luna_persona') != 'haddock-repartie-v3':
    raise SystemExit('Verified ready bot with current persona required')
vault = Path('/srv/obsidian/vault/VPS')
expected = {'Services/Veille des cours UPB.md':'437b08fbc42a83a65198aa379ab6c3f869047737d8f652d00d31966510e80897',
            'Notes/VPS - architecture et exploitation.md':'da658d066abb707e58bc15f8d89987f0d0ffde324dde4c86e23e729b689269a3'}
original = {name:(vault/name).read_bytes() for name in expected}
if any(hashlib.sha256(content).hexdigest() != expected[name] for name,content in original.items()):
    raise SystemExit('Notes changed since review; no write performed')
paragraph = '''
#### Incident du 12 novembre : réponse inexploitable et pause générale

À 05:33, heure de Bucarest, le client Discord était connecté, sans redémarrage automatique ni erreur de livraison, mais le rapport Luna indiquait `invalid_response`. Le code regroupait les réponses refusées, incomplètes et invalides dans ce seul diagnostic ; toute `LunaError` déclenchait ensuite une pause de 60 secondes pour le salon. Les messages suivants recevaient donc la phrase de radio en panne sans nouvel appel, malgré une connexion Discord fonctionnelle. Le rejeu du message référencé, avec contexte borné au salon/rôle UPB et sans publication Discord, a renvoyé un résultat valide. Le sous-type exact du premier résultat n’est pas récupérable à partir de l’ancien rapport ; aucune transcription privée n’a été ajoutée aux journaux.

Correctif déployé : validation séparée des refus (`model_refusal`), limites de sortie, réponses incomplètes, absence de texte, JSON invalide, schéma invalide et réponse vide. Ces problèmes restent limités au message concerné : bref retour local, sans pause générale et sans relance automatique d’un refus. Les erreurs réelles API/réseau conservent leur pause de protection. `/status` distingue la dernière réponse inexploitable d’une panne de liaison ; les diagnostics persistés contiennent uniquement un code, un périmètre et une heure. Une réponse refusée n’est pas une panne de connexion. Les tokens API effectivement utilisés restent comptés même si le résultat est rejeté. Quota de 1 M/jour, reset Bucarest et réservations séparées conservés.

Les **88 tests** passent, dont réponse invalide suivie immédiatement d’un autre message traité, refus sans réessai ni pause globale, vrai HTTP 429 avec temporisation et usage compté pour les résultats rejetés. Client redémarré et prêt, commandes et boutons vérifiés. `diagnose_luna_failure.py` produit des métadonnées sans textes ; sans option, il ne lance aucun appel OpenAI. Son option `--probe` transmet uniquement un contexte récent autorisé pour un appel de diagnostic compté dans le quota, sans publier de message ni remplacer le rapport de santé du client. Ne pas le lancer périodiquement. [Gestion officielle des refus et réponses incomplètes](https://developers.openai.com/api/docs/guides/structured-outputs?api-mode=responses).
'''
notes = {name:content.decode('utf-8') for name,content in original.items()}
service = 'Services/Veille des cours UPB.md'
notes[service] = notes[service].replace('84 tests couvrent','88 tests couvrent',1)
notes[service] = notes[service].replace('### Conversations contextuelles avec Luna\n','### Conversations contextuelles avec Luna\n'+paragraph,1)
notes['Notes/VPS - architecture et exploitation.md'] += '\n\n### Incident Luna : validation et reprise du salon\n'+paragraph
if any((vault/name).read_bytes() != content for name,content in original.items()):
    raise SystemExit('Concurrent note change; no write performed')
for name,note in notes.items(): (vault/name).write_text(note,encoding='utf-8')
print(json.dumps({'obsidian_updated':True,'notes':2,'recovery':'message_error_does_not_pause_channel'}))
