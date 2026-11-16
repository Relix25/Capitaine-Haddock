from pathlib import Path
import hashlib
import shutil

vault=Path('/srv/obsidian/vault/VPS')
index=vault/'Accueil VPS.md'
architecture=vault/'Notes/VPS - architecture et exploitation.md'
expected={index:'98ef7045612cee7cd4eb896d55e4c6ae4a88c200086e406f0ab110712adce34b',
          architecture:'2e75b4b606b27b5e5a4907247f2a077d2eb1dcedce3ac247f9dfec4c8e4b4c89'}
for path,value in expected.items():
    if hashlib.sha256(path.read_bytes()).hexdigest()!=value:
        raise RuntimeError('Note changed since review; documentation stopped to preserve concurrent changes: '+str(path))
target=vault/'Services/Veille des cours UPB.md'
if target.exists():
    raise RuntimeError('A service note already exists; not overwritten')
shutil.copyfile(Path(__file__).with_name('Veille des cours UPB.md'),target)
text=index.read_text()
text=text.replace('updated: 2026-11-02','updated: 2026-11-12',1)
text=text.replace('## Applications et données\n','## Applications et données\n\n- [[Veille des cours UPB]] — service installé, connexions Moodle/Teams et destination Discord encore à configurer.\n',1)
index.write_text(text)
text=architecture.read_text()
text=text.replace('updated: 2026-11-02','updated: 2026-11-12',1)
text+='''\n\n## Veille des cours UPB\n\nInstallation et contrôles techniques du 12 novembre 2026 : [[Veille des cours UPB]]. Compte isolé `courswatch`, code `/opt/cours-watch`, données privées `/var/lib/cours-watch`. Le timer de 08:00 Europe/Bucharest est installé mais sa condition d'activation n'est pas satisfaite : les huit sources demandent encore une authentification et Discord n'est pas configuré. Aucun cookie transféré ni message envoyé. Le test du navigateur avec sandbox et les sept tests de fonctionnement passent.\n\nUne proposition d'accès Web temporaire pour la connexion reste soumise à accord ; la tentative précédente a été refusée automatiquement et n'a pas modifié Nginx. Les règles SSH, AppArmor, UFW et les permissions d'OpenCode/Kiro restent inchangées. Les secrets du moniteur restent hors du coffre et ses sauvegardes éventuelles doivent également rester privées.\n'''
architecture.write_text(text)
print('Service note, index and architecture updated without secrets.')
