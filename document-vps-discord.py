"""Targeted documentation update; refuse concurrent edits and never read secrets."""
from pathlib import Path
import hashlib

vault = Path('/srv/obsidian/vault/VPS')
index = vault / 'Accueil VPS.md'
architecture = vault / 'Notes/VPS - architecture et exploitation.md'
service = vault / 'Services/Veille des cours UPB.md'
expected = {
    index: '5f114283e77c43964dc3bee8c9ff7314dc120572334e322a783d8fe953ccd167',
    architecture: '26d0a47078b437bb8f463ef467996c9637921fcb1870070a7d92938aec53d498',
    service: '8eb99a738bfda1dadb6716d722eb75031b3054ac45b303daf2614ef5dbca43fd',
}
for path, checksum in expected.items():
    if hashlib.sha256(path.read_bytes()).hexdigest() != checksum:
        raise RuntimeError('Concurrent note change; stopped: ' + str(path))
text = index.read_text()
old = '- [[Veille des cours UPB]] — service installé, connexions Moodle/Teams et destination Discord encore à configurer.'
if text.count(old) != 1:
    raise RuntimeError('Index layout changed')
new_index = text.replace(old, '- [[Veille des cours UPB]] — Discord Capitaine Haddock et rôle UPB testés ; connexions Moodle/Teams et collecte à valider, accès privé temporaire approuvé.')
text = architecture.read_text()
marker = '\n## Veille des cours UPB\n'
if text.count(marker) != 1:
    raise RuntimeError('Architecture layout changed')
prefix, previous = text.split(marker)
if not previous.rstrip().endswith('ses sauvegardes éventuelles doivent également rester privées.'):
    raise RuntimeError('Unexpected trailing notes; stopped')
new_architecture = prefix + marker + '''
Installation et contrôles du 12 novembre 2026 : [[Veille des cours UPB]]. Compte isolé `courswatch`, code `/opt/cours-watch`, données privées `/var/lib/cours-watch`. Le timer de 08:00 Europe/Bucharest est installé mais reste bloqué par l'absence de `/etc/cours-watch/ready` : les huit sources attendent une connexion et la lecture authentifiée doit encore être validée. Aucun cookie local transféré. Le navigateur avec sandbox et huit tests de fonctionnement ont été validés.

Discord : webhook Capitaine Haddock et rôle UPB configurés. Le message de test du 12 novembre à 01:01, heure de Bucarest, a été accepté avec la mention du rôle ; aucune alerte de collecte authentifiée n'a encore été envoyée. Les expressions nautiques conservent les consignes, dates et liens. Le jeton et les sessions restent hors du coffre ; reçu non secret `/etc/cours-watch/discord-test.json`.

Après refus de la première proposition permanente, l'utilisateur a explicitement approuvé l'accès temporaire `/cours-watch/` sur `https://relix.vps:8443`, limité au client WireGuard `10.77.77.2` avec identifiants dédiés. Cette version est appliquée ; fermeture automatique programmée le 12 novembre à 01:47:18, heure de Bucarest, ou après connexion. Le profil de session est conservé après fermeture. SSH, AppArmor, UFW et permissions OpenCode/Kiro n'ont pas été assouplis. Les limites de couverture et les étapes restantes sont dans la fiche du service.
'''
new_service = Path('/tmp/cours-watch-service-note.md').read_text()
if '1570211962342613003' not in new_service or 'status: Discord testé' not in new_service:
    raise RuntimeError('Unexpected service note')
service.write_text(new_service)
index.write_text(new_index)
architecture.write_text(new_architecture)
print('Three Obsidian notes updated; no secrets copied.')
