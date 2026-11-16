"""Document the verified concise persona and truthful usage display, preserving unrelated notes."""
import hashlib
import json
from pathlib import Path

proof = json.loads(Path('/tmp/cours-watch-bot-proof.json').read_text())
if not (proof.get('bot_ready') and proof.get('luna_persona') == 'haddock-repartie-v3'
        and proof.get('luna_daily_token_limit') == 1_000_000):
    raise SystemExit('Active concise persona with unchanged quota required')
vault = Path('/srv/obsidian/vault/VPS')
expected = {'Services/Veille des cours UPB.md':'8ea053def189f268f639d9c77e7eb6e147955fd5ef2239957d10ec0924ca0720',
            'Accueil VPS.md':'ac20dc68fb3c234b44a403982e471e5619f592f665a933a3d37f517f5c3cdfad',
            'Notes/VPS - architecture et exploitation.md':'c99c027f58ade4c52bc9b405e829ac1c00995e24da8ea5ff487cf0856f61b606'}
original = {name:(vault/name).read_bytes() for name in expected}
if any(hashlib.sha256(content).hexdigest() != expected[name] for name,content in original.items()):
    raise SystemExit('Notes changed since review; no write performed')
paragraph = '''
#### Profil courant concis et compteur corrigé — 12 novembre

Profil actif `haddock-repartie-v3` : pour le chambrage, une phrase courte (généralement 8–20 mots), une seule pique, aucune métaphore par défaut. Les détails utiles restent possibles si demandés. Le fil d’une joute est conservé : « j’en ai gros sur la patate » ou une grossièreté visant le bot ne suffisent pas à lancer automatiquement un discours de soutien ou à annoncer l’arrêt des jurons. Les demandes explicites d’arrêt et les vrais contextes sérieux restent distingués. Sept appels réels au total, dont cinq tours du fil de la capture fournie et deux contrôles, ont produit cinq répliques brèves, une réponse à l’arrêt explicite et une réponse sérieuse adaptée. Évaluations incluses dans le quota. Le client a été redémarré et le profil actif vérifié ; ce réglage remplace les consignes de style v2 décrites plus haut.

Après signalement d’un écart de tokens, l’audit a confirmé que les appels terminés utilisaient entrée + sortie une seule fois. Le vrai défaut d’affichage était le mélange de cette consommation avec la réservation conservatrice d’un appel en cours ou incertain. `/status` affiche maintenant séparément **tokens confirmés par l’API** et **réservations non confirmées**. Le plafond garde les deux pour éviter un dépassement ; les réservations ne sont plus présentées comme une consommation connue. Les champs sont `used_tokens`, `reserved_tokens` et `budget_used_tokens` ; `remaining_tokens` tient compte des provisions.

Le total `usage.total_tokens` est conservé et utilisé lorsqu’il est fourni ; les lignes historiques utilisent entrée + sortie. Le cache fait déjà partie de l’entrée, sans second ajout. Migration SQLite conservant tous les usages et le quota, avec métadonnées de total API et cache pour les nouveaux appels ; pas de texte ou d’auteur ajouté au journal. Le contrôle réel minimal a renvoyé **1 621 tokens d’entrée (dont 1 568 en cache) + 42 de sortie = 1 663**, identique au total API et à l’incrément du compteur. Le relevé local suivant était de **41 126 = 39 314 + 1 812**, sur 28 réponses confirmées, sans provision restante. Les tests API de mise au point et analyses silencieuses sont inclus.

La vue Usage du navigateur affichait à ce contrôle **22 778 tokens et 17 requêtes**, Default project, toutes clés, sept derniers jours. Elle ne recensait donc pas le même nombre d’appels que les 28 réponses du VPS ; la concordance globale avec cette vue n’est pas établie à cet instant. Ne pas diviser arbitrairement le compteur, soustraire le cache du quota ou effacer l’historique. Le calcul par réponse est vérifié et l’affichage des provisions est corrigé. [Documentation officielle des tokens](https://developers.openai.com/api/docs/guides/token-counting).

**84 tests passent**, dont migration de l’ancien compteur, cache non doublé, consommation réelle, provision en cours distincte, concurrence, persistance et minuit à Bucarest. Quota conservé : 1 000 000 de tokens par jour, entrée + sortie, reset à minuit Europe/Bucharest. État `reports/luna-status.json` ; `audit_luna_usage.py` contrôle les métadonnées sans appel payant. Les scripts `smoke_luna_repartie.py` et `probe_luna_usage.py` sont des vérifications API manuelles payantes, pas des tâches périodiques.
'''
notes = {name:content.decode('utf-8') for name,content in original.items()}
service = 'Services/Veille des cours UPB.md'
notes[service] = notes[service].replace('Soixante-trois tests couvrent','84 tests couvrent',1).replace('Quatre-vingts tests couvrent','84 tests couvrent',1)
notes[service] = notes[service].replace('### Conversations contextuelles avec Luna\n','### Conversations contextuelles avec Luna\n'+paragraph,1)
notes['Notes/VPS - architecture et exploitation.md'] += '\n\n### Répartie concise et comptage des tokens\n'+paragraph
notes['Accueil VPS.md'] = notes['Accueil VPS.md'].replace('conversations Luna au ton Haddock plus mordant et chambrage burlesque,',
    'conversations Luna concises au ton Haddock, chambrage burlesque et réservations séparées des tokens consommés,',1)
if any((vault/name).read_bytes() != content for name,content in original.items()):
    raise SystemExit('Concurrent note change; no write performed')
for name,note in notes.items(): (vault/name).write_text(note,encoding='utf-8')
print(json.dumps({'obsidian_updated':True,'notes':3,'persona':'haddock-repartie-v3','usage_display':'confirmed_and_reserved_separated'}))
