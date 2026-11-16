"""Record the verified task command and private source repository, preserving note history."""
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile


def main():
    proof = json.loads(Path('/tmp/cours-watch-bot-proof.json').read_text())
    if not proof.get('bot_ready') or 'a-faire' not in proof.get('live_commands', []):
        raise SystemExit('Live command verification required')
    root = Path('/srv/obsidian/vault/VPS')
    service = root / 'Services/Veille des cours UPB.md'
    index = root / 'Accueil VPS.md'
    expected = {
        service:'4475b82b1cab897e56293dd9e21c853c0b5d1e7d2fde02c0beee27724a895513',
        index:'4245db2c6c8413c2a0b9fae14510c5f709ebc006bf8390d8221aaabbe28c4835',
    }
    original = {path:path.read_bytes() for path in expected}
    for path,data in original.items():
        if hashlib.sha256(data).hexdigest() != expected[path]:
            raise SystemExit('Concurrent note edit; stopped')
    text = original[service].decode('utf-8')
    text += '''

## Liste des tâches et dépôt du code — 17 novembre 2026

La commande `/a-faire [cours]` est enregistrée dans l’API Discord et le client actif a été vérifié prêt après redémarrage. Elle reprend les tâches configurées des rappels avec leurs dates revalidées dans le dernier cache, de l’échéance la plus récente à la plus ancienne. Dix tâches sont actuellement affichables sur deux pages privées de cinq, avec actions et liens de consignes/planning. Filtre par cours, boutons Précédent/Suivant ; le demandeur et le rôle UPB sont recontrôlés. Aucun ping ni collecte automatique déclenché par cette commande.

Les dates à confirmer sont placées après les dates connues. Les échéances passées portent « rendu à vérifier », sans inférence sur un dépôt ou passage à l’oral personnel. Les dates sans heure restent sans heure, et 00:00 est indiqué en début de journée. La liste porte sur les tâches suivies des rappels, pas sur une extraction exhaustive de nouveaux devoirs. Les rappels automatiques conservent leur fonctionnement antérieur et excluent les dates passées.

Validation : 59 tests réussis sur le VPS, puis 24 tests directement concernés après une retouche de l’affichage de l’emoji et correction d’une erreur de syntaxe introduite par cette retouche. Les quatre commandes sont retrouvées dans l’API, le bot et les composants existants sont prêts. Aucun nouveau test public ni ping ; le contrôle technique ne remplace pas une confirmation humaine de la nouvelle pagination. Sauvegarde d’avant commande : `/var/backups/cours-watch/tasks-20261117`.

Le code et les informations d’exploitation sont dans le dépôt GitHub privé [Capitaine-Haddock](https://github.com/Relix25/Capitaine-Haddock), premier commit `41a40f64b0f6213885207bff6ae83b7c29942d72`. Aucun historique de code antérieur n’existait localement ou sous `/opt/cours-watch` ; aucun commit ancien n’a été reconstitué. Premier import : 188 fichiers, dont sources, tests, outils, unités systemd, assets, exemples sans secrets et documentation. La recherche des formats courants de secrets n’a trouvé aucun élément à exporter ; `.env`, cookies, profils, bases, rapports privés et documents de cours sont exclus.

La copie Git de travail est `C:/Users/basti/Documents/ChatGPT/Cours/Outils/Capitaine-Haddock`, dossier exclu du partage du coffre. Les prochaines modifications de code se versionnent dans ce dépôt ; le VPS conserve sa copie installée dans `/opt/cours-watch`, sans déploiement automatique. Aucun identifiant GitHub n’a été transféré sur le VPS. Documentation d’installation et de maintenance dans le dépôt ; les installateurs ponctuels historiques restent soumis à leurs préconditions.
'''
    listing = original[index].decode('utf-8')
    old = '/cours, /actualiser et /status (santé, prochains envois et confirmations)'
    new = '/a-faire (tâches triées par date, en privé), /cours, /actualiser et /status (santé, prochains envois et confirmations)'
    if listing.count(old) != 1:
        raise SystemExit('Unexpected index content')
    listing = listing.replace(old,new,1).replace('updated: 2026-11-15','updated: 2026-11-17',1)
    listing += '\nCode du bot : dépôt privé [Capitaine-Haddock](https://github.com/Relix25/Capitaine-Haddock), historique commencé le 17 novembre 2026.\n'
    for path,data in original.items():
        if path.read_bytes() != data:
            raise SystemExit('Concurrent note edit; stopped')
    for path,content in ((service,text),(index,listing)):
        fd,name = tempfile.mkstemp(prefix='.haddock-task-',dir=path.parent)
        try:
            with os.fdopen(fd,'w',encoding='utf-8') as stream:
                stream.write(content)
            os.chmod(name,0o660)
            os.replace(name,path)
        finally:
            Path(name).unlink(missing_ok=True)
    print(json.dumps({'documented':True,'notes':2,'command':'a-faire','repo_private':True}))


def record_layout_revision():
    proof = json.loads(Path('/tmp/haddock-task-layout-proof.json').read_text())
    if not proof.get('ready') or proof.get('sort') != 'upcoming_nearest_first' or not proof.get('one_task_per_card'):
        raise SystemExit('Verified revised layout required')
    root = Path('/srv/obsidian/vault/VPS')
    service = root / 'Services/Veille des cours UPB.md'
    index = root / 'Accueil VPS.md'
    original = {path:path.read_bytes() for path in (service,index)}
    text = original[service].decode('utf-8')
    marker = '### Révision de lisibilité et de priorité — 17 novembre 2026'
    if marker in text or 'Liste des tâches et dépôt du code — 17 novembre 2026' not in text:
        raise SystemExit('Unexpected documentation state')
    text += '\n\n' + marker + '''

Après le premier push, l’utilisateur a précisé que le premier élément devait être l’échéance à venir la plus proche du moment de la commande et que l’affichage était trop condensé. Cette révision remplace le tri décroissant et les cinq tâches par carte décrits dans la section précédente.

`/a-faire` affiche désormais les dates futures croissantes, une seule tâche par carte privée : date visible, temps restant, action séparée et boutons « Ouvrir les consignes » / « Choisir un créneau » si un planning existe. Les boutons Précédent/Suivant changent aussi les liens. Les dates passées sont masquées par défaut ; `passees: vrai` les ajoute après les tâches à venir, de la plus proche à la plus ancienne. Les dates à confirmer restent en fin de liste. Les échéances passées ne sont pas assimilées à des rendus manquants.

Contrôle du cache réel : six tâches à venir, dix en incluant les dates passées ; première carte AAIT, oral Crafter du 24 novembre, heure non publiée. Les 61 tests passent sur le VPS ; client prêt et option Discord `passees` enregistrée. Aucun nouveau ping ou message public de test. Sauvegarde : `/var/backups/cours-watch/tasks-layout-20261117`. Code et documentation poussés dans le dépôt privé, commit `46b8eb05e1d6c2791a25831c4437cae5ff012307`, sans réécriture du premier import.
'''
    listing = original[index].decode('utf-8')
    old = '/a-faire (tâches triées par date, en privé)'
    if listing.count(old) != 1:
        raise SystemExit('Unexpected index content')
    listing = listing.replace(old,'/a-faire (prochaine échéance d’abord, une tâche par carte privée)',1)
    for path,data in original.items():
        if path.read_bytes() != data:
            raise SystemExit('Concurrent note edit')
    for path,content in ((service,text),(index,listing)):
        fd,name = tempfile.mkstemp(prefix='.haddock-layout-',dir=path.parent)
        try:
            with os.fdopen(fd,'w',encoding='utf-8') as stream:
                stream.write(content)
            os.chmod(name,0o660)
            os.replace(name,path)
        finally:
            Path(name).unlink(missing_ok=True)
    print(json.dumps({'documented':True,'notes':2,'layout':'one_task_per_card','order':'upcoming_nearest_first'}))


if __name__ == '__main__':
    if sys.argv[1:] == ['--layout-revision']:
        record_layout_revision()
    elif not sys.argv[1:]:
        main()
    else:
        raise SystemExit('Unknown arguments')
