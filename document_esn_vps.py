"""Document the verified ESN service with guarded master-vault edits."""
import hashlib
import json
import os
from pathlib import Path
import tempfile

ROOT=Path('/srv/obsidian/vault/VPS')
EXPECTED={'Accueil VPS.md':'496df32e3549fdf97a7b34bce2291f7c8e608c536e972aa9cb156c1b4c7c0850',
          'Notes/VPS - architecture et exploitation.md':'bead92f0247b3f11e2f12436fcc5d0735b62f194cc140b9d2a3201732953e95f'}


def atomic(path,text):
    fd,name=tempfile.mkstemp(prefix='.esn-note-',dir=path.parent)
    try:
        with os.fdopen(fd,'w',encoding='utf-8') as f: f.write(text)
        os.chmod(name,0o660); os.replace(name,path)
    finally: Path(name).unlink(missing_ok=True)


def main():
    proof=json.loads(Path('/tmp/esn-watch-proof.json').read_text())
    report=proof['report']
    if report.get('status')!='ok' or not proof['bot_ready']:
        raise SystemExit('A successful live check and a ready bot are required')
    if proof['units']['cours-watch-esn.timer']['ActiveState']!='active':
        raise SystemExit('The timer must be active')
    source={}
    for relative,digest in EXPECTED.items():
        path=ROOT/relative; raw=path.read_bytes()
        if hashlib.sha256(raw).hexdigest()!=digest: raise SystemExit('Concurrent edit: '+relative)
        source[relative]=raw.decode('utf-8')
    target=ROOT/'Services/ESN Bucarest - Linktree.md'
    if target.exists() or target.is_symlink(): raise SystemExit('ESN note already exists; read/review before editing')
    state='Pings actifs sur le rôle ESN.' if proof.get('role_id') else 'Diffusion en attente : le rôle ESN doit être créé et configuré. Le bot ne possède pas Gérer les rôles ; aucun élargissement de droits effectué.'
    note=f'''---
type: service
tags: [vps, discord, esn, veille]
verified: 2026-11-15
---
# ESN Bucarest — Linktree

[[Accueil VPS]] · [[Veille des cours UPB]] · [[VPS - architecture et exploitation]]

## Fonctionnement

Surveillance de [BucharestEvents](https://linktr.ee/BucharestEvents) toutes les **5 minutes** sur le VPS, même PC éteint. Une nouvelle destination publiée produit une carte compacte par Capitaine Haddock dans le salon UPB configuré, avec bouton vers l’activité et mention du seul rôle **ESN**. Les mentions UPB/everyone/here ne sont pas activées par ce module.

La première collecte initialise silencieusement les liens déjà présents. Réordonnancement, changement de titre, paramètres de suivi et réapparition d’une URL connue ne produisent pas de nouveau ping. Une URL nouvelle sous un identifiant réutilisé est détectée. Retraits et contenu interne des formulaires ne sont pas surveillés. Aucun formulaire n’est rempli et aucune inscription n’est effectuée.

## État vérifié

- Contrôle réel : {report.get('last_success_utc')}.
- {report.get('active_links')} liens actifs ; {proof['known_urls']} destinations connues.
- {proof['confirmed']} envois confirmés ; {proof['pending']} en attente.
- {state}
- Le client Discord est prêt. `/status` affiche désormais le contrôle ESN, la fraîcheur, les envois et l’attente du rôle.

## Exploitation

- Code : `/opt/cours-watch/esn_watch.py` ; configuration sans secret : `/etc/cours-watch/esn.json`.
- Planification : `cours-watch-esn.timer` ; exécution : `cours-watch-esn.service`, compte `courswatch`, réseau sortant uniquement.
- Référence et file persistantes : `/var/lib/cours-watch/esn.db` ; rapport : `/var/lib/cours-watch/reports/esn-status.json`.
- Le jeton du bot est lu depuis son emplacement privé existant. Aucun compte, session Moodle/Teams, clé OpenAI, port entrant ou accès au coffre supplémentaire n’est utilisé.
- Activer le rôle existant et envoyer une seule confirmation explicite : `sudo /opt/cours-watch/venv/bin/python /opt/cours-watch/configure_esn.py --role-id ID_ESN --announce`. Le rôle doit s’appeler ESN et être mentionnable ; ses membres se gèrent dans Discord. Cette commande conserve le rôle UPB et les commandes du cours.
- Contrôle immédiat : `sudo systemctl start cours-watch-esn.service` ; journaux : `sudo journalctl -u cours-watch-esn.service -n 30 --no-pager`.
- Désactiver : `sudo systemctl disable --now cours-watch-esn.timer`. Conserver la base pour éviter de réannoncer les anciennes activités à la reprise.

## Validation et limites

23 tests ciblés passent : référence initiale silencieuse, nouveaux liens, même ID avec URL nouvelle, changements de titres/ordre, liens historiques ignorés, reprise après redémarrage, file conservée en cas d’erreur, nonce stable, mentions limitées et état ESN dans `/status`. La lecture du vrai Linktree a été vérifiée sous systemd. L’accès HTTP brut renvoie 403, donc le module utilise Chrome normalement, sans contournement de protection ni session personnelle.

Une panne de page ou de structure conserve la référence et est affichée dans `/status`. Les messages attendent la configuration du rôle. Un événement retiré entre deux passages peut échapper à une surveillance périodique. Les reçus confirment l’acceptation par Discord, pas la lecture des utilisateurs ; le nonce limite les doublons de reprise.
'''
    index=source['Accueil VPS.md']
    anchor='## Applications et données\n'
    if index.count(anchor)!=1: raise SystemExit('Unexpected index structure')
    label='pings ESN actifs' if proof.get('role_id') else 'rôle ESN en attente'
    index=index.replace(anchor,anchor+'\n- [[ESN Bucarest - Linktree]] — nouveautés Linktree toutes les cinq minutes ; '+label+'.\n',1)
    architecture=source['Notes/VPS - architecture et exploitation.md']
    architecture+='\n\n## Veille ESN Bucarest — 15 novembre 2026\n\nSurveillance indépendante du Linktree public toutes les cinq minutes via `cours-watch-esn.timer` et `.service`, sous `courswatch`, avec le même bot Discord. Référence silencieuse, détection par URL canonique et file SQLite persistante ; aucune session de cours ni API OpenAI utilisée. Dernier contrôle réel : '+str(report.get('last_success_utc'))+' ; '+str(report.get('active_links'))+' liens actifs. '+state+' `/status` expose cette distinction. Fiche et procédure : [[ESN Bucarest - Linktree]]. 23 tests ciblés passent ; aucun port ouvert ni permission élargie.\n'
    for relative in EXPECTED:
        if hashlib.sha256((ROOT/relative).read_bytes()).hexdigest()!=EXPECTED[relative]: raise SystemExit('Concurrent edit')
    atomic(target,note)
    atomic(ROOT/'Accueil VPS.md',index)
    atomic(ROOT/'Notes/VPS - architecture et exploitation.md',architecture)
    print(json.dumps({'documented':True,'delivery_ready':bool(proof.get('role_id'))}))


if __name__=='__main__': main()
