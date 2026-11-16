"""Document the approved Relix role assignment and verified manual Linktree test."""
import hashlib
import json
import os
from pathlib import Path
import tempfile


def main():
    receipt=json.loads(Path('/tmp/esn-current-test-proof.json').read_text())
    if receipt.get('status')!='confirmed' or receipt.get('role_ping_count')!=1:
        raise SystemExit('Verified manual test required')
    if receipt.get('role_id')!='1571515589501915308': raise SystemExit('Wrong role')
    root=Path('/srv/obsidian/vault/VPS')
    service=root/'Services/ESN Bucarest - Linktree.md'
    index=root/'Accueil VPS.md'
    original={path:path.read_bytes() for path in (service,index)}
    text=original[service].decode('utf-8')
    marker='### Attribution autorisée et test des liens actuels'
    if marker in text: raise SystemExit('Test already documented')
    text=text.replace('Attribution aux personnes en attente de confirmation explicite.',
                      'Rôle attribué à Relix après son accord, puis vérifié dans l’interface Discord : un membre.')
    message=receipt['messages'][0]
    text+=f'''\n{marker} — 15 novembre 2026\n\nL’utilisateur a répondu « Oui tu peux » et demandé un test avec les annonces actuelles du Linktree dans le salon. L’autorisation a été appliquée au destinataire le plus restreint, Relix uniquement. L’interface de Campus Campus ! confirme `Manage Members (1)` et Relix dans le rôle ESN. Aucun autre membre n’a été ajouté. Le refus d’approbation décrit dans l’historique précédent est ainsi résolu par l’accord utilisateur.\n\nLecture publique effectuée à {receipt['checked_utc']} : {receipt['active_links']} liens actuels. Un [message de test]({message['url']}) contient {message['embeds']} cartes avec liens et boutons, clairement marquées TEST, avec un seul ping ESN. Le reçu Discord et sa relecture confirment les liens, le nombre de cartes et la mention du rôle, sans mention UPB/everyone/here. Les envois de test ne prouvent pas la lecture humaine.\n\nLe test est distinct des nouvelles activités : il ne modifie ni la référence de surveillance ni sa file. Le reçu privé est dans `reports/esn-current-links-test-20261115-current-1.json`. L’outil `send_esn_current_test.py` exige un identifiant de test et refuse de répéter une tentative existante ; il n’est pas planifié.\n'''
    listing=original[index].decode('utf-8').replace('rôle ESN créé, destinataires à confirmer.',
        'rôle ESN actif pour Relix, test des liens actuels confirmé.')
    listing=listing.replace('updated: 2026-11-12','updated: 2026-11-15',1)
    for path,data in original.items():
        if hashlib.sha256(path.read_bytes()).digest()!=hashlib.sha256(data).digest(): raise SystemExit('Concurrent edit')
    for path,content in ((service,text),(index,listing)):
        fd,name=tempfile.mkstemp(prefix='.esn-current-test-',dir=path.parent)
        try:
            with os.fdopen(fd,'w',encoding='utf-8') as stream: stream.write(content)
            os.chmod(name,0o660); os.replace(name,path)
        finally: Path(name).unlink(missing_ok=True)
    print(json.dumps({'documented':True,'recipient':'Relix','cards':message['embeds'],'role_pings':1}))


if __name__=='__main__': main()
