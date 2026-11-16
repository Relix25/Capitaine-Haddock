"""Record the verified requested two-second cooldown without rewriting history."""
import json
from pathlib import Path

proof = json.loads(Path('/tmp/cours-watch-bot-proof.json').read_text())
if not proof.get('bot_ready') or proof.get('easter_egg_cooldown_seconds') != 2:
    raise SystemExit('Verified active two-second cooldown required')
vault = Path('/srv/obsidian/vault/VPS')
service = vault / 'Services/Veille des cours UPB.md'
architecture = vault / 'Notes/VPS - architecture et exploitation.md'
original = {path:path.read_bytes() for path in (service,architecture)}
note = original[service].decode('utf-8')
for before,after in [('au moins 30 secondes entre deux réponses','au moins 2 secondes entre deux réponses'),
                     ('cooldown_seconds: 30','cooldown_seconds: 2'),
                     ('Le délai de 30 secondes est commun aux deux formats','Le délai de 2 secondes est commun aux deux formats')]:
    if note.count(before) != 1:
        raise SystemExit('Unexpected cooldown description; no write performed')
    note = note.replace(before,after,1)
detail = original[architecture].decode('utf-8')
detail += "\n\nRéglage demandé le 12 novembre : délai de l'easter egg réduit de 30 à **2 secondes**, commun aux réponses texte et vocales. Configuration appliquée, client redémarré et vérifié prêt avec cooldown_seconds=2 ; cinq samples et probabilité vocale de 50 % conservés. Les durées de 30 secondes mentionnées plus haut décrivent le réglage antérieur.\n"
if any(path.read_bytes() != content for path,content in original.items()):
    raise SystemExit('Concurrent note change; no write performed')
service.write_text(note)
architecture.write_text(detail)
print('Verified two-second cooldown documented in Obsidian.')
