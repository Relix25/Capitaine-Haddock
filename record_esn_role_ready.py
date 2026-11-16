"""Record the verified role creation; do not assign it to any member."""
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile


def main():
    proof=json.loads(Path('/tmp/esn-watch-proof.json').read_text())
    if proof.get('role_id')!='1571515589501915308' or proof['report'].get('delivery')!='ok':
        raise SystemExit('Verified ESN configuration required')
    receipt=json.loads(Path('/tmp/esn-role-receipt.json').read_text())
    if not receipt.get('role_mention_confirmed'): raise SystemExit('Real role mention required')
    root=Path('/srv/obsidian/vault/VPS')
    service=root/'Services/ESN Bucarest - Linktree.md'
    index=root/'Accueil VPS.md'
    originals={p:p.read_bytes() for p in (service,index)}
    text=originals[service].decode('utf-8')
    old='Diffusion en attente : le rôle ESN doit être créé et configuré. Le bot ne possède pas Gérer les rôles ; aucun élargissement de droits effectué.'
    new='Rôle **ESN** (`1571515589501915308`) créé dans **Campus Campus !** par Computer Use, mentionnable et lié au bot. Le compte du bot ne reçoit aucun droit de gestion des rôles. Attribution aux personnes en attente de confirmation explicite.'
    if old not in text: raise SystemExit('Unexpected service note state')
    text=text.replace(old,new,1)
    text=text.replace('2026-11-15T12:29:21+00:00',proof['report']['last_success_utc'])
    text=text.replace('Les messages attendent la configuration du rôle.',
                      'Le rôle est configuré ; seuls ses membres recevront ses pings.')
    text+='\n### Création du rôle dans Discord — 15 novembre 2026\n\nRôle ESN enregistré par l’interface de Campus Campus !. Le [message de confirmation](https://discord.com/channels/1376984512046563519/1570209088929800282/'+receipt['message_id']+') est accepté par Discord et sa mention ESN est confirmée dans le reçu. Le contrôle suivant réussit avec `delivery: ok`, six liens actifs et zéro doublon. Aucun membre n’a été ajouté : la sélection de Relix a été refusée par l’approbation automatique, faute de destinataire explicitement autorisé. La question de destinataires est laissée à l’utilisateur.\n'
    listing=originals[index].decode('utf-8')
    listing=listing.replace('nouveautés Linktree toutes les cinq minutes ; rôle ESN en attente.',
                            'nouveautés Linktree toutes les cinq minutes ; rôle ESN créé, destinataires à confirmer.')
    for path,data in originals.items():
        if hashlib.sha256(path.read_bytes()).digest()!=hashlib.sha256(data).digest(): raise SystemExit('Concurrent note edit')
    for path,content in ((service,text),(index,listing)):
        fd,name=tempfile.mkstemp(prefix='.esn-role-note-',dir=path.parent)
        try:
            with os.fdopen(fd,'w',encoding='utf-8') as stream: stream.write(content)
            os.chmod(name,0o660); os.replace(name,path)
        finally: Path(name).unlink(missing_ok=True)
    print(json.dumps({'role_documented':True,'member_assignment':'awaiting_confirmation'}))


if __name__=='__main__': main()
