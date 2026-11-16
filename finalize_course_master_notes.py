"""Finalize the migration notes on the master, guarded by verified replica hashes."""
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import pwd
import tempfile
from zoneinfo import ZoneInfo

ROOT = Path('/srv/obsidian/cours')


def atomic(path, data):
    fd,name=tempfile.mkstemp(prefix='.migration-note-',dir=path.parent)
    try:
        with os.fdopen(fd,'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(name,0o660)
        os.replace(name,path)
    finally:
        Path(name).unlink(missing_ok=True)


def main():
    import sys
    if os.geteuid()!=pwd.getpwnam('vaultagent').pw_uid:
        raise RuntimeError('Run as vaultagent')
    proof_path=Path(sys.argv[1])
    proof=json.loads(proof_path.read_text(encoding='utf-8'))
    originals={}
    for name,digest in proof['root_note_hashes'].items():
        path=ROOT/name
        if path.is_symlink(): raise RuntimeError('Unexpected symbolic link')
        data=path.read_bytes()
        if hashlib.sha256(data).hexdigest()!=digest:
            raise RuntimeError('Concurrent note edit: '+name)
        originals[name]=data
    checked=datetime.fromisoformat(proof['collection']['checked_utc']).astimezone(ZoneInfo('Europe/Bucharest'))
    stamp=checked.strftime('%d/%m/%Y à %H:%M:%S')
    docs=proof['pdfs']['documents']
    pages=proof['pdfs']['pages']
    sync=f'''---
type: synchronisation
updated: 2026-11-12
status: opérationnel
---
# Synchronisation du coffre

[[Accueil Cours|Accueil]] · [[Veille - dernier contrôle|Dernière collecte]] · [[Sources et couverture|Couverture]]

**Opérationnel et vérifié le 12 novembre 2026.** Le coffre maître est sur le VPS ; le PC reçoit sa copie par Syncthing sur WireGuard.

| Rôle | Chemin | Mode |
|---|---|---|
| Maître VPS | `/srv/obsidian/cours` | `sendonly` |
| Copie PC | `C:\\Users\\basti\\Documents\\ChatGPT\\Cours` | `receiveonly` |

Le partage est `obsidian-cours`. Les modifications persistantes se font dans le coffre VPS. Une modification locale du PC ne remonte pas au maître et peut créer une divergence à traiter avant réception. Les réglages Obsidian restent locaux.

Le transfert initial de **{proof['archive']['files']} fichiers** a été contrôlé par SHA256. La collecte réelle du **{stamp} (Bucarest)** a ensuite publié les huit sources Moodle/Teams, **{docs} PDF / {pages} pages** et leur texte intégral ; leurs fichiers et les cinq pages automatiques ont été vérifiés sur le PC. À la vérification : aucun fichier en attente, aucune divergence locale, aucune erreur de réception. [Preuve structurée](<Sources/Actualisation_2026-11-12/migration-verification.json>).

## Mises à jour

La collecte s’exécute chaque jour à **08:00, heure de Bucarest**, même lorsque le PC est éteint. `/actualiser` dans Discord déclenche également la collecte. Après chaque collecte, le VPS actualise [[Veille - dernier contrôle]], les quatre pages de veille et les index [[AAIT - Versions des PDF]], [[AITDM - Versions des PDF]], [[BDFA - Versions des PDF]], [[SOS - Versions des PDF]]. Le PC reçoit les changements lorsqu’il est allumé avec Syncthing et WireGuard connectés. Syncthing démarre à l’ouverture de session Windows.

Les PDF Moodle accessibles jusqu’à 25 Mio sont retéléchargés avec revalidation du cache. La comparaison porte sur **SHA256 et nombre de pages**, même si le nom et l’URL ne changent pas. Les anciennes versions sont conservées ; le texte de chaque page et les variations de pages figurent dans les index. Une modification sans changement de pagination reste détectée par SHA256. Les dates d’archivage ne constituent pas des dates de publication.

## Contenu et limites

Les originaux, vidéos, projets, notes et preuves pédagogiques sont copiés. `.git`, `.obsidian`, `Outils` et `.extraction` sont exclus ; les preuves utiles de `.extraction` ont été copiées sous `Sources` et leurs liens adaptés, avec sauvegarde des anciennes notes dans le dossier technique local.

Les notes automatiques donnent les éléments effectivement collectés et distinguent les items frais des items conservés. Les synthèses Luna restent datées ; elles ne sont pas réécrites par la collecte. Teams couvre Général uniquement ; fichiers non annoncés, autres canaux et Classwork ne sont pas garantis. Aucun dépôt ni état de présence personnel n’est déduit. Aucun site, formulaire ou document original n’a été modifié.
'''
    accueil=originals['Accueil Cours.md'].decode('utf-8')
    accueil=accueil.replace('## À regarder en premier\n',
        '## À regarder en premier\n\n**Coffre maître VPS actif, copie PC synchronisée.** Pour les données les plus récentes, ouvrir [[Veille - dernier contrôle]]. Les synthèses ci-dessous restent datées. Les index [[AAIT - Versions des PDF]], [[AITDM - Versions des PDF]], [[BDFA - Versions des PDF]] et [[SOS - Versions des PDF]] donnent les PDF versionnés et le texte par page.\n',1)
    sources=originals['Sources et couverture.md'].decode('utf-8')
    sources=sources.replace('L’inventaire actuel est donc de **20 PDF et 1 104 pages**.',
        'Cet audit initial portait donc sur **20 PDF et 1 104 pages**. La collecte réelle suivante a ajouté un PDF Crafter de six pages ; l’inventaire courant est de **21 PDF et 1 110 pages**.')
    sources=sources.replace('| AAIT | 6 PDF, 418 pages (dont *AAIT Project Guidelines.pdf*, 22 pages) |',
        '| AAIT | 7 PDF, 424 pages (dont le guide de 22 pages et une présentation Crafter de 6 pages) |')
    sources += f'''\n## Coffre maître et contrôle de fin de migration\n\nLa collecte réelle du **{stamp} (Bucarest)** a réussi sur les huit sources. Le maître `/srv/obsidian/cours` publie maintenant {docs} PDF/{pages} pages, leurs textes intégraux et quatre relevés automatiques. Le PDF supplémentaire `Calmets_Kilian.pdf` (six pages) est une présentation de projet jointe à Crafter ; son ajout au corpus ne constitue pas une nouvelle deadline. Voir [[AAIT - Versions des PDF]], [[Veille - dernier contrôle]] et [[Synchronisation du coffre]]. Les dates de collecte des versions ne sont pas des dates de publication des enseignants.\n'''
    actual=originals['Actualisation - 12 novembre 2026.md'].decode('utf-8')
    actual += f'''\n## Contrôle réel après migration\n\n**{stamp}, Bucarest : 8/8 sources OK.** Les données du dernier contrôle sont désormais dans [[Veille - dernier contrôle]]. Les 20 PDF de l’audit initial restent conservés ; une présentation Crafter de six pages a été récupérée en pièce jointe, portant le catalogue à **{docs} PDF / {pages} pages**. Les versions et le texte de toutes les pages sont disponibles dans [[AAIT - Versions des PDF]], [[AITDM - Versions des PDF]], [[BDFA - Versions des PDF]], [[SOS - Versions des PDF]]. Le VPS est maître et la réception réelle sur PC a été vérifiée : [[Synchronisation du coffre]].\n'''
    output={'Synchronisation du coffre.md':sync,'Accueil Cours.md':accueil,
        'Sources et couverture.md':sources,'Actualisation - 12 novembre 2026.md':actual}
    # Preserve the full pre-finalization prose on the master before replacement.
    history=ROOT/'Sources/Actualisation_2026-11-12/avant-finalisation'
    history.mkdir(parents=True,exist_ok=True)
    for name,data in originals.items():
        backup=history/(name+'.bak')
        if backup.exists() and backup.read_bytes()!=data:
            raise RuntimeError('Unexpected previous backup')
        if not backup.exists(): atomic(backup,data)
    for name,text in output.items():
        if (ROOT/name).read_bytes()!=originals[name]: raise RuntimeError('Concurrent edit')
        atomic(ROOT/name,text.encode())
    evidence=ROOT/'Sources/Actualisation_2026-11-12/migration-verification.json'
    if evidence.exists(): raise RuntimeError('Evidence already exists')
    atomic(evidence,proof_path.read_bytes())
    print(json.dumps({'master_notes_updated':list(output),'proof_copied':True},ensure_ascii=False))


if __name__ == '__main__':
    main()
