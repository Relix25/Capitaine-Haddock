"""Update only reviewed monitor notes after successful activation; preserve concurrent edits."""
from pathlib import Path
import hashlib
import json

proof = json.loads(Path('/tmp/cours-watch-activation.json').read_text())
if len(proof['sources']) != 8 or any(s['status'] != 'ok' for s in proof['sources']) or not proof['timers_active']:
    raise SystemExit('Verified activation required')
vault = Path('/srv/obsidian/vault/VPS')
index = vault / 'Accueil VPS.md'
architecture = vault / 'Notes/VPS - architecture et exploitation.md'
service = vault / 'Services/Veille des cours UPB.md'
expected = {
    index: 'effa1964826fd9d5f74de426dbde4e5b173d50fbc92b0d32cfa390808bb26c80',
    architecture: '1241ba47f7ee5e580d8e3141b2d80683d12ba6de345a3a88e0d6b627c9d3ef08',
    service: 'b6e56e9522a171e61aca073e45964ec855789ceab9cdca57b7008494513d9c69',
}
for path, checksum in expected.items():
    if hashlib.sha256(path.read_bytes()).hexdigest() != checksum:
        raise SystemExit('Concurrent edit; notes not overwritten: ' + str(path))
text = index.read_text()
old = '- [[Veille des cours UPB]] — Discord Capitaine Haddock et rôle UPB testés ; connexions Moodle/Teams et collecte à valider, accès privé temporaire approuvé.'
if text.count(old) != 1:
    raise SystemExit('Unexpected index layout')
new_index = text.replace(old, '- [[Veille des cours UPB]] — Moodle/Teams actifs, contrôle quotidien à 08:00 Europe/Bucharest, alertes Capitaine Haddock avec rôle UPB ; accès de connexion fermé.')
text = architecture.read_text()
marker = '\n## Veille des cours UPB\n'
if text.count(marker) != 1:
    raise SystemExit('Unexpected architecture layout')
prefix, previous = text.split(marker)
if not previous.rstrip().endswith('Les limites de couverture et les étapes restantes sont dans la fiche du service.'):
    raise SystemExit('Unexpected trailing architecture notes')
new_architecture = prefix + marker + '''
Installation et activation vérifiées le 12 novembre 2026 : [[Veille des cours UPB]]. Compte isolé `courswatch`, code `/opt/cours-watch`, profil et état privés `/var/lib/cours-watch`. Les huit sources Moodle/Teams des quatre cours sont authentifiées et la collecte réelle sous systemd a réussi. `cours-watch.timer` est actif pour 08:00 Europe/Bucharest, avec rattrapage ; `/etc/cours-watch/ready` est présent. Neuf tests passent. Administrativ est exclu.

Discord : webhook Capitaine Haddock et rôle UPB configurés et testés. Les expressions nautiques conservent les consignes, dates et liens. Les alertes Moodle de la mise en route ont été suivies de leur rétablissement après correction de la persistance. Les références de contenu initiales sont silencieuses ; les ajouts et modifications suivants sont envoyés via une file SQLite avec reprise. Jeton et sessions hors du coffre.

Les cookies du seul navigateur Moodle du VPS sont conservés dans `/var/lib/cours-watch/moodle-cookies.json`, propriétaire courswatch, mode 0600 ; aucun cookie du navigateur personnel n'a été exporté. Ils sont restaurés après initialisation de Chrome en remplaçant les cookies anonymes. `cours-watch-session.timer` maintient la session par une lecture HTTP protégée toutes les vingt minutes, sans navigateur ; une révocation ou expiration absolue peut toujours exiger une reconnexion. Navigateurs limités à 3 Gio après un arrêt mémoire initial ; verrou commun et nettoyage du marqueur de connexion même après arrêt anormal.

La route de connexion temporaire `/cours-watch/` et ses identifiants HTTP sont fermés et retirés après usage ; profil et sessions conservés. Lors d'une reconnexion demandée, cette seule interface peut être rouverte pour le client WireGuard 10.77.77.2 avec identifiants dédiés et fermeture automatique en une heure maximum. SSH, AppArmor, UFW et permissions OpenCode/Kiro n'ont pas été assouplis. Couverture et limites détaillées dans la fiche du service.
'''
note = Path('/tmp/cours-watch-service-note.md').read_text()
if 'status: opérationnel' not in note:
    raise SystemExit('Unexpected service note')
note += '\n\nDernière collecte complète vérifiée : `' + proof['checked_utc'] + '` (UTC), **' + str(proof['items']) + ' éléments** sur huit sources. Instantané de validation, pas un compteur en temps réel.\n'
service.write_text(note)
index.write_text(new_index)
architecture.write_text(new_architecture)
print('Service, index and architecture updated from verified activation; no secrets copied.')
