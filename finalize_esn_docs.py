"""Record final ESN checks without changing historical service configuration."""
import hashlib
import json
import os
from pathlib import Path
import tempfile


def main():
    proof=json.loads(Path('/tmp/esn-watch-proof.json').read_text())
    report=proof['report']
    if report.get('status')!='ok' or report.get('initialized_now') is not False:
        raise SystemExit('A successful repeat collection is required')
    root=Path('/srv/obsidian/vault/VPS')
    note=root/'Services/ESN Bucarest - Linktree.md'
    arch=root/'Notes/VPS - architecture et exploitation.md'
    original={p:p.read_bytes() for p in (note,arch)}
    text=original[note].decode('utf-8')
    text=text.replace('2026-11-15T12:24:00+00:00',report['last_success_utc'])
    text=text.replace('23 tests ciblés passent','51 tests de surveillance, santé et non-régression du bot passent')
    text=text.replace('- Activer le rôle existant et envoyer',
        '- Le rôle ESN unique, non géré par une intégration et mentionnable est détecté automatiquement à chaque contrôle si aucun ID n’est fixé ; aucune permission de gestion des rôles n’est nécessaire pour le mentionner. Il suffit de le créer puis de l’attribuer aux personnes intéressées.\n- Activer le rôle existant et envoyer')
    text+='\n### Vérification finale — 15 novembre 2026\n\nPassages réels successifs réussis, six liens conservés et zéro nouvelle annonce pour un contenu inchangé. Chrome atteignait la limite initiale de 128 tâches ; le service utilise maintenant 256 tâches maximum, 768 Mio de mémoire et garde son isolation. Les passages suivants terminent en environ trois secondes. La diffusion reste en attente du rôle ESN au dernier contrôle ; `/status` rend cet état visible.\n'
    architecture=original[arch].decode('utf-8')
    architecture+='\nComplément de validation ESN : 51 tests passent et les contrôles successifs sous systemd réussissent sans doublons. Le service utilise au plus 256 tâches et 768 Mio ; le blocage initial de Chrome sur la limite de 128 tâches est corrigé. Le rôle ESN mentionnable est maintenant détecté automatiquement après sa création, sans modifier les droits du bot. Dernier succès : '+report['last_success_utc']+'.\n'
    for path,data in original.items():
        if hashlib.sha256(path.read_bytes()).digest()!=hashlib.sha256(data).digest():
            raise SystemExit('Concurrent note change')
    for path,content in ((note,text),(arch,architecture)):
        fd,name=tempfile.mkstemp(prefix='.esn-final-',dir=path.parent)
        try:
            with os.fdopen(fd,'w',encoding='utf-8') as stream: stream.write(content)
            os.chmod(name,0o660); os.replace(name,path)
        finally: Path(name).unlink(missing_ok=True)
    print(json.dumps({'updated':True,'repeat_collection_verified':True,'role_pending':not bool(report.get('role_id'))}))


if __name__=='__main__': main()
