"""Update reviewed monitor notes after verified real Discord delivery, preserving other notes."""
from pathlib import Path
import hashlib
import json

proof = json.loads(Path('/tmp/cours-watch-reminder-proof.json').read_text())
if proof['sources'] != 8 or len(proof['initial_messages']) != 4 or proof['initial_pings'] != 1:
    raise SystemExit('Live delivery proof required')
vault = Path('/srv/obsidian/vault/VPS')
index = vault / 'Accueil VPS.md'
architecture = vault / 'Notes/VPS - architecture et exploitation.md'
service = vault / 'Services/Veille des cours UPB.md'
expected = {service:'8ed50be5074de3b10187b33dbfbc4a99a51f5da071cf280544459c541e99b571',
            index:'51131c11dc789d1407896d01bacfc938a2106343d728277425772422134b53f8',
            architecture:'7ddfa385cb220f507ae8839966f50088b7a846892656026cfe4baeba8f9b598c'}
for path, checksum in expected.items():
    if hashlib.sha256(path.read_bytes()).hexdigest() != checksum:
        raise SystemExit('Concurrent change; stopped without overwriting: ' + str(path))
text = index.read_text()
old = '- [[Veille des cours UPB]] — Moodle/Teams actifs, contrôle quotidien à 08:00 Europe/Bucharest, alertes Capitaine Haddock avec rôle UPB ; accès de connexion fermé.'
if text.count(old) != 1:
    raise SystemExit('Index layout changed')
new_index = text.replace(old, '- [[Veille des cours UPB]] — nouveautés et rappels des projets à 08:00 Europe/Bucharest, IMPORTANT à J-3 / URGENT à J-1 ; Capitaine Haddock, rôle UPB, accès de connexion fermé.')
text = architecture.read_text()
marker = '\n## Veille des cours UPB\n'
if text.count(marker) != 1:
    raise SystemExit('Architecture layout changed')
prefix, section = text.split(marker)
if 'Neuf tests passent.' not in section:
    raise SystemExit('Unexpected monitor section')
section = section.replace('Neuf tests passent.', 'Dix-neuf tests passent.',1)
section += '''

Rappels des projets ajoutés le 12 novembre : dix échéances et présentations dans `/etc/cours-watch/reminders.json`. Après chaque collecte de 08:00, le bot rappelle les tâches à venir même sans nouveauté, avec IMPORTANT à J-3, URGENT à J-1 ou le jour même, dates/actions/liens et une seule mention UPB par lot. Les heures Moodle sont reprises telles qu'affichées ; aucune heure n'est inventée. Les dates Moodle sont relues, les calendriers d'annonces/PDF sont liés à une empreinte et passent à confirmer si leur source change. Les échéances passées n'impliquent aucun retard personnel.

Le premier point réel a été accepté par Discord en quatre messages le 12 novembre à 01:48, heure de Bucarest, et le second déclenchement n'a rien renvoyé. Les clés du point initial sont distinctes de celles du rappel de 08:00. Reçus de livraison sans secret dans SQLite (`discord_receipts`). Les fils de forum sont normalisés pour éviter les faux changements de liens ; les fichiers Moodle sont lus par GET, avec première empreinte silencieuse ; paragraphes et listes chargés sur Teams sont collectés. Les documents et sites sources restent inchangés.
'''
note = Path('/tmp/cours-watch-service-note.md').read_text()
if 'Dix-neuf tests' not in note or '## Rappels quotidiens des projets' not in note:
    raise SystemExit('Unexpected service note')
note += '\n\nDernière collecte réelle vérifiée : `' + proof['checked_utc'] + '` (UTC), **' + str(proof['items']) + ' éléments** sur huit sources, **' + str(proof['document_fingerprints']) + ' empreintes de documents**. Changements au dernier passage : ' + str(proof['latest_changes']) + '. Instantané de contrôle, pas un compteur en temps réel.\n'
service.write_text(note)
index.write_text(new_index)
architecture.write_text(prefix + marker + section)
print('Three Obsidian service notes updated from real collection and Discord receipts.')
