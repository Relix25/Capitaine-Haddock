"""Record the verified Luna architecture without credentials or conversation transcripts."""
import hashlib
import json
from pathlib import Path

proof = json.loads(Path('/tmp/cours-watch-bot-proof.json').read_text())
if not (proof.get('bot_ready') and proof.get('luna_enabled') and proof.get('luna_model') == 'gpt-6-luna'
        and proof.get('luna_daily_token_limit') == 1_000_000):
    raise SystemExit('Verified active Luna with the requested token ceiling required')

vault = Path('/srv/obsidian/vault/VPS')
service = vault/'Services/Veille des cours UPB.md'
index = vault/'Accueil VPS.md'
architecture = vault/'Notes/VPS - architecture et exploitation.md'
expected = {service:'75c3cca34f8ad9bef0c4c1b5566d24e415cbafe727a67ef4c032e29e79443038',
            index:'d903703fce978bc451633a97d998578c4f2f43c6db1f7fc2cf9a3d0bed2d7ac7',
            architecture:'0bdfb0c128b64f700ebe44d5bc55e379d66d3d5445ba20df42d6c192cb7ef7f3'}
original = {path:path.read_bytes() for path in expected}
if any(hashlib.sha256(content).hexdigest() != expected[path] for path,content in original.items()):
    raise SystemExit('Notes changed since review; no write performed')

section = '''
### Conversations contextuelles avec Luna

Le 12 novembre, l’utilisateur a autorisé l’usage de sa clé OpenAI pour les conversations du salon privé, puis fixé un plafond de **1 000 000 de tokens par jour, entrée + sortie**, réinitialisé à **minuit Europe/Bucharest**. Le modèle actif est `gpt-6-luna`, via Responses, reasoning `none`, sortie JSON structurée et `store: false`. La veille et la traduction continuent à fonctionner localement ; seule la conversation utilise OpenAI.

Une mention du bot, une réponse à son message, une adresse « Haddock, … » ou un ancien mot déclencheur isolé lance une vraie réponse dans son personnage. La même personne peut poursuivre pendant deux minutes sans répéter son nom. Les nouveaux messages du seul serveur/salon/rôle UPB alimentent un contexte en RAM : au plus 30 messages, 30 minutes et environ 18 Ko, avec textes bornés et masquage des formats de clés/jetons reconnus. Ce contexte, dont les pseudonymes, est envoyé à OpenAI lors des appels. Aucun historique Discord antérieur n’est téléchargé. Le contexte est perdu au redémarrage, sans journal local de texte ou d’auteur. `store: false` n’est pas une garantie d’absence de journaux de service chez le fournisseur.

Haddock garde le silence par défaut dans les conversations entre camarades ; Luna décide d’intervenir seulement pour une bonne remarque contextuelle, drôle ou utile. Analyse spontanée au plus toutes les 30 secondes après une courte pause ; publication spontanée au plus quatre fois par heure, avec trois minutes entre interventions et 90 secondes après sa dernière réponse. Les réponses directes gardent deux secondes minimum d’écart et une seule requête est en vol. Les messages rapides sont regroupés, avec priorité aux sollicitations directes. Les questions sur les cours peuvent inclure les faits du cache de projets en lecture seule ; aucune date ne doit être inventée.

Les cinq samples existants restent utilisables lorsque Luna juge leur thème adapté : dans ce cas, 50 % de chances d’un vocal, sinon la réplique texte préparée. Une réponse explicative n’est pas remplacée au hasard par un son. En cas d’erreur audio, retour au texte. Aucune synthèse ou imitation de voix ajoutée.

Le compteur est persistant dans `/var/lib/cours-watch/luna-usage.db` (mode 0600), avec uniquement jours, états et nombres de tokens. Les analyses silencieuses comptent aussi. Une réservation atomique conservatrice avant chaque requête empêche un dépassement ; elle est remplacée par l’usage API réel connu. Un timeout garde sa réservation par prudence. Si le prochain appel risque de dépasser le plafond, les appels sont bloqués jusqu’au lendemain, y compris après redémarrage ; le compteur peut donc afficher légèrement moins d’un million au blocage. Aucun plafond monétaire distinct n’est ajouté.

À la limite, une sollicitation reçoit sans API : « Mille sabords ! J’ai englouti ma ration de mots pour aujourd’hui. Le capitaine est en cale sèche jusqu’à minuit, heure de Bucarest ! » Les échanges ambiants ne produisent qu’un avis de quota par jour. Une panne API donne une phrase de radio en panne en réponse directe, avec une pause de 60 secondes avant un nouvel essai. `/cours`, `/actualiser`, `/status`, les boutons, traductions et rappels ne dépendent pas de ce quota.

La clé reste dans `/etc/cours-watch/.env`, champ `OPENAI_API_KEY`, root:courswatch 0640, lue uniquement par le module conversation ; elle n’est pas exportée vers les processus de navigateur/traduction. Aucun nouvel intent, port, compte ou droit au coffre n’a été ajouté. État métadonnées : `reports/luna-status.json` et `reports/bot-status.json`. `/status` lit le compteur du jour sans appel API et affiche disponibilité, modèle, consommation entrée + sortie et reset à minuit.

Configuration : `discord.luna.enabled`, `model: gpt-6-luna`, `daily_token_limit: 1000000` dans `/etc/cours-watch/config.json`. Pour couper seulement l’IA : `enabled: false` dans cette section, puis `sudo systemctl restart cours-watch-bot.service` ; l’ancien easter egg local redevient disponible si sa propre section reste activée. Ne pas supprimer le compteur pour remettre artificiellement le quota à zéro. Après modification de la clé, redémarrer le client. `smoke_luna.py` est un test API payant explicite avec messages inventés et décompte dans le vrai quota ; ne pas l’exécuter en boucle.

Validation du 12 novembre : **80 tests** couvrent la veille et le bot, dont blocage sans requête, persistance, réservations concurrentes, timeout, minuit à Bucarest, confidentialité locale, priorité directe, cadence, voix et état du compteur. Trois vrais appels avec messages fictifs ont répondu aux questions, gardé le contexte et ignoré un échange banal : **2 482 tokens** au total. Client actif, prêt et modèle/plafond vérifiés ; `/status` a été prévisualisé avec les huit sources OK et zéro annonce en attente. Le quota a été éprouvé avec une petite limite simulée, sans consommer un million de tokens pour le test.

Documentation officielle consultée : [GPT-6 Luna](https://developers.openai.com/api/docs/models/gpt-6-luna.md), [sorties structurées](https://developers.openai.com/api/docs/guides/structured-outputs.md?api-mode=responses), [état des conversations](https://developers.openai.com/api/docs/guides/conversation-state.md).
'''

text = {path:content.decode('utf-8') for path,content in original.items()}
text[service] = text[service].replace('Soixante-trois tests couvrent','Quatre-vingts tests couvrent',1)
text[service] = text[service].replace('### Easter egg Haddock','### Easter egg Haddock — fonctionnement local de secours',1)
text[service] = text[service].replace('Le code et le mode opératoire complets sont dans le projet Cours : `Outils/veille-cours/`. Aucun fournisseur LLM ni changement des permissions d\'OpenCode/Kiro n\'est nécessaire pour cette version du moniteur.',
    'Le code et le mode opératoire complets sont dans le projet Cours : `Outils/veille-cours/`. Veille et traduction locales ; conversations via OpenAI selon la section Luna. Les permissions OpenCode/Kiro ne sont pas modifiées.')
text[service] = text[service].replace('## Connexion temporaire fermée après usage',section+'\n## Connexion temporaire fermée après usage',1)
text[index] = text[index].replace('easter egg texte/vocal (50 %),','conversations Luna au ton Haddock, interventions spontanées rares, quota de 1 M tokens/jour et samples contextuels (50 %),',1)
text[architecture] += '\n\n## Conversations Luna du bot de cours — 12 novembre 2026\n\nVoir [[Veille des cours UPB]] pour le fonctionnement courant et la procédure. Les paragraphes précédents décrivent la progression du service ; le mode conversation Luna remplace les réponses par simple mot-clé lorsque `discord.luna.enabled` est vrai.\n'+section
if any(path.read_bytes() != content for path,content in original.items()):
    raise SystemExit('Concurrent note change; no write performed')
for path,note in text.items():
    path.write_text(note,encoding='utf-8')
print(json.dumps({'obsidian_updated':True,'notes':3,'secrets_written':False}))
