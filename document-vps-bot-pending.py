"""Record the installed interactive client accurately while authentication is pending."""
from pathlib import Path
import hashlib

vault = Path('/srv/obsidian/vault/VPS')
service = vault / 'Services/Veille des cours UPB.md'
index = vault / 'Accueil VPS.md'
architecture = vault / 'Notes/VPS - architecture et exploitation.md'
expected = {service:'ecc8683b98dea9aec359a1db1c3d84e3774def50665e1d07d068863dc3934ebc',
            index:'faa7fa23caa1d789f7a7707c6e4095114348c7b3ebfc138f9b3500bf7692c82f',
            architecture:'f520fc2b1df2f477353ebe5b68513bb9a7cd890969f89403f5ea3f04cb3496d9'}
for path, checksum in expected.items():
    if hashlib.sha256(path.read_bytes()).hexdigest() != checksum:
        raise SystemExit('Concurrent edit; stopped without overwriting: ' + str(path))
text = index.read_text()
old = '- [[Veille des cours UPB]] — nouveautés et rappels des projets à 08:00 Europe/Bucharest, IMPORTANT à J-3 / URGENT à J-1 ; Capitaine Haddock, rôle UPB, accès de connexion fermé.'
if text.count(old) != 1:
    raise SystemExit('Index layout changed')
new_index = text.replace(old, '- [[Veille des cours UPB]] — embeds compacts, rappels à 08:00 et urgence J-3/J-1 ; application Discord préparée pour commandes et détails privés, jeton à valider ; accès de connexion fermé.')
text = architecture.read_text()
marker = '\n## Veille des cours UPB\n'
if text.count(marker) != 1:
    raise SystemExit('Architecture layout changed')
prefix, section = text.split(marker)
section = section.replace('Dix-neuf tests passent.', 'Vingt-huit tests passent.',1)
section += '''

Présentation Discord mise à jour le 12 novembre : les quatre messages initiaux sont des embeds compacts, modifiés sans nouvelle mention. Application 1510767148006641866 prévue pour `/cours`, `/actualiser` et bouton « Plus d’infos » privé. Code et unité `cours-watch-bot.service` installés, environnement séparé `/opt/cours-watch/bot-venv` avec discord.py 2.7.1 ; activation en attente d'un Bot Token validé. Le webhook reste le transport opérationnel pendant cette étape.

Le `.env` demandé est `/etc/cours-watch/.env`, root:courswatch 0640, champ DISCORD_BOT_TOKEN, lu directement sans export aux processus du navigateur. Le webhook reste dans secrets.json. Aucun secret n'est dans le coffre. Le client prévu ouvre seulement une connexion Gateway sortante ; pas de port public ni changement Nginx. Autorisation des interactions : serveur, salon et rôle UPB ; détails éphémères pour le seul demandeur. Les cartes privées sont liées à leur auteur. Pas d'intents privilégiés Message Content, Members ou Presence. Marqueur de démarrage `/etc/cours-watch/bot-ready` et basculement vers bot_channel seulement après vérification des commandes et permissions.
'''
note = Path('/tmp/cours-watch-service-note.md').read_text()
if '## Embeds compacts et application interactive' not in note or 'Vingt-huit tests' not in note:
    raise SystemExit('Unexpected service note')
service.write_text(note)
index.write_text(new_index)
architecture.write_text(prefix + marker + section)
print('Three Obsidian notes now distinguish live embeds from pending interactive activation; no secrets copied.')
