#!/usr/bin/env python3
"""Audit and (only with --apply) document the course master on the VPS."""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

HOST = "ubuntu@10.77.77.1"
HOST_KEY = "SHA256:7esY8zTalzR/gidyJpsPlYQH8SzwnEEIoXLuckdSgOM"
KEY = r"C:\Users\basti\Documents\ovh-priv.ppk"
PLINK = r"C:\Program Files\PuTTY\plink.exe"
REMOTE_ROOT = "/srv/obsidian/vault"
EXPECTED = {
    "AGENTS.md": "45bb420d9bcef111fa4092f2118959b46986e4507b44e970a951a96640a8b18f",
    "VPS/Notes/VPS - architecture et exploitation.md": "5bd76f63bfc4b720a47e79b47c0ab7d63dae940689dddae2c7c489d3680f1b54",
    "VPS/Accueil VPS.md": "dc9609a08d0c76a19508e623cca33a8ed2721115726bdf692edce88e59d30f5e",
    "VPS/Services/Syncthing et Obsidian.md": "c7d3f511daad062939833feb2e31d8247b68fc666dfca1996435c5062aa23bba",
}
TARGET = "VPS/Services/Cours - coffre maître.md"

AUDIT_CODE = r'''import base64,hashlib,json,os,pathlib
root=pathlib.Path('/srv/obsidian/vault')
paths=['AGENTS.md','VPS/Notes/VPS - architecture et exploitation.md','VPS/Accueil VPS.md','VPS/Services/Syncthing et Obsidian.md','VPS/Services/Cours - coffre maître.md']
out={}
for rel in paths:
 p=root/rel
 if p.is_symlink(): raise SystemExit('symlink refusé: '+rel)
 if p.exists():
  if not p.is_file(): raise SystemExit('type de fichier inattendu: '+rel)
  b=p.read_bytes(); out[rel]={'sha256':hashlib.sha256(b).hexdigest(),'bytes':len(b),'content_b64':base64.b64encode(b).decode('ascii')}
  if rel.endswith('Cours - coffre maître.md'): out[rel]['exists']=True
 else: out[rel]={'exists':False}
print(json.dumps(out,ensure_ascii=False))'''

APPLY_CODE = r'''import hashlib,json,os,pathlib,tempfile
root=pathlib.Path('/srv/obsidian/vault').resolve()
data=json.load(__import__('sys').stdin)
expected=data['expected']; files=data['files']
if os.geteuid()!=__import__('pwd').getpwnam('vaultagent').pw_uid: raise SystemExit('utilisateur inattendu')
def safe(rel):
 p=root
 for part in pathlib.PurePosixPath(rel).parts:
  p=p/part
  if p.is_symlink(): raise SystemExit('symlink refusé: '+rel)
 return p
for rel,want in expected.items():
 p=safe(rel)
 if not p.is_file() or hashlib.sha256(p.read_bytes()).hexdigest()!=want: raise SystemExit('hashguard refusé: '+rel)
target=root/'VPS/Services/Cours - coffre maître.md'
if target.exists() or target.is_symlink(): raise SystemExit('la fiche cible existe déjà')
for rel in files:
 p=safe(rel)
 if rel=='VPS/Services/Cours - coffre maître.md':
  if not p.parent.is_dir(): raise SystemExit('parent cible absent')
 elif not p.is_file(): raise SystemExit('chemin refusé: '+rel)
# Prépare toutes les écritures avant de remplacer quoi que ce soit.
staged=[]
try:
 for rel,content in files.items():
  p=safe(rel)
  fd,tmp=tempfile.mkstemp(prefix='.cours-doc-',dir=str(p.parent))
  with os.fdopen(fd,'wb') as f:
   f.write(content.encode('utf-8')); f.flush(); os.fsync(f.fileno())
  os.chmod(tmp,0o660); staged.append((pathlib.Path(tmp),p))
 for tmp,p in staged: os.replace(tmp,p)
 print(json.dumps({'applied':list(files)},ensure_ascii=False))
finally:
 for tmp,_ in staged:
  try: tmp.unlink()
  except FileNotFoundError: pass'''


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def run_remote(code: str, stdin: str | None = None) -> str:
    encoded = base64.b64encode(code.encode("utf-8")).decode("ascii")
    remote = f"sudo -n -u vaultagent -g vault python3 -c \"import base64;exec(base64.b64decode('{encoded}'))\""
    cmd = [PLINK, "-batch", "-ssh", "-hostkey", HOST_KEY, "-i", KEY, HOST, remote]
    p = subprocess.run(cmd, input=stdin, text=True, encoding="utf-8", capture_output=True)
    if p.returncode:
        raise RuntimeError((p.stderr or p.stdout).strip() or f"plink exit {p.returncode}")
    return p.stdout.strip()


def validate_proof(proof: dict) -> tuple[str, str]:
    if proof.get("version") != 1 or not isinstance(proof.get("verified_utc"), str):
        raise ValueError("preuve: version=1 et verified_utc requis")
    sync, archive, collection = (proof.get(k) for k in ("syncthing", "archive", "collection"))
    if not all(isinstance(x, dict) for x in (sync, archive, collection)):
        raise ValueError("preuve: syncthing, archive et collection requis")
    if sync.get("folder_id") != "obsidian-cours" or sync.get("vps_mode") != "sendonly" or sync.get("pc_mode") != "receiveonly":
        raise ValueError("preuve Syncthing incohérente")
    for k in ("need_files", "need_bytes", "receive_only_changed_files", "pull_errors"):
        if sync.get(k) != 0: raise ValueError(f"preuve Syncthing: {k} doit valoir 0")
    if not isinstance(archive.get("sha256"), str) or len(archive["sha256"]) != 64 or archive.get("hashes_verified") is not True:
        raise ValueError("preuve archive: SHA-256 et hashes_verified=true requis")
    pdfs = proof.get("pdfs")
    if not isinstance(pdfs, dict) or pdfs.get("documents", 0) < 20 or pdfs.get("pages", 0) < 1104:
        raise ValueError("preuve PDFs: détails réels requis, minimum attendu 20 documents / 1104 pages")
    guide = next((x for x in pdfs.get("extra_guides", []) if x.get("filename") == "AAIT Project Guidelines.pdf"), None)
    if not guide or guide.get("pages") != 22 or len(guide.get("sha256", "")) != 64:
        raise ValueError("preuve PDFs: version AAIT Project Guidelines (22 pages, SHA-256) requise")
    if not isinstance(collection.get("sources"), list) or not collection["sources"]:
        raise ValueError("preuve collection: sources avec états requis")
    if not all(isinstance(s, dict) and s.get("source") and s.get("status") for s in collection["sources"]):
        raise ValueError("preuve collection: chaque source doit avoir source et status")
    return proof["verified_utc"], proof["collection"].get("checked_utc", proof["verified_utc"])


def render(proof: dict, state: dict) -> dict[str, str]:
    verified, checked = validate_proof(proof)
    stamp = datetime.fromisoformat(verified.replace("Z", "+00:00"))
    if stamp.tzinfo is None: raise ValueError("verified_utc doit contenir un fuseau")
    try:
        date = stamp.astimezone(ZoneInfo("Europe/Bucharest")).strftime("%Y-%m-%d")
    except KeyError:
        date = stamp.strftime("%Y-%m-%d")
    syn, arc, coll, pdf = proof["syncthing"], proof["archive"], proof["collection"], proof["pdfs"]
    sources = "\n".join(f"- `{s.get('source','?')}` : **{s.get('status','inconnu')}** — {s.get('detail','') }" for s in coll["sources"])
    guide = next((x for x in pdf.get("extra_guides", []) if "AAIT Project Guidelines.pdf" in x.get("filename", "")), None)
    guide_line = f"\nGuide AAIT : `{guide['filename']}`, {guide['pages']} pages, SHA-256 `{guide['sha256']}`." if guide else ""
    note = f'''---
type: service
tags: [vps, service, cours, obsidian]
verified: {date}
---

# Cours — coffre maître

## Rôle et synchronisation

Le coffre maître des cours est `/srv/obsidian/cours`. Il est distinct du coffre de documentation VPS `/srv/obsidian/vault`. Le partage Syncthing `obsidian-cours` est configuré en **sendonly sur le VPS** et **receiveonly sur le PC**.

Dernière preuve : {verified}. État PC : `{syn.get('pc_state','inconnu')}` ; {syn.get('global_files','?')} fichiers / {syn.get('global_bytes','?')} octets ; fichiers manquants {syn.get('need_files','?')}, erreurs de pull {syn.get('pull_errors','?')} ; appareil `{syn.get('connected_device','?')}` via `{syn.get('connected_via','?')}`.

Archive vérifiée : SHA-256 `{arc['sha256']}`, {arc.get('files','?')} fichiers et {arc.get('bytes','?')} octets ; tous les hashes contrôlés : oui.

## Collecte et publication

La collecte planifiée est quotidienne à 08:00 Europe/Bucharest ; commande manuelle : `/actualiser`. Après collecte, l’export géré est déclenché par `cours-watch-obsidian.path` et exécuté par `cours-watch-obsidian.service`. Les documents PDF archivés se trouvent dans `/var/lib/cours-watch-publish/documents`.

Dernier contrôle : {checked}. Sources vérifiées :
{sources}

Le catalogue actuel contient {pdf['documents']} PDF pour {pdf['pages']} pages.{guide_line}

Les PDF Moodle accessibles jusqu’à 25 Mio sont retéléchargés avec revalidation du cache ; SHA256 et nombre de pages sont contrôlés même à nom/URL identiques. Les versions sont immuables sous `Documents - versions` et leur texte intégral par page est publié sous `Notes/<cours>/Documents`. Une augmentation du nombre de pages reste visible dans l’index des versions. Les dates d’archivage ne constituent pas des dates de publication du professeur.

## Portée des notes et limites

Les synthèses rédigées manuellement par Luna sont datées et restent distinctes des notes automatiques complètes. Les notes automatiques ne remplacent pas les synthèses. Teams est surveillé dans **General uniquement**. Les états de collecte décrivent les sources consultées, sans garantir l’exhaustivité des contenus ni l’état du travail personnel.
'''
    def existing(rel: str) -> str:
        return base64.b64decode(state[rel]["content_b64"]).decode("utf-8")
    index = existing("VPS/Accueil VPS.md")
    if "[[Cours - coffre maître]]" not in index:
        anchor = "## Applications et données\n"
        if index.count(anchor) != 1: raise ValueError("index: ancre Applications et données absente/ambiguë")
        index = index.replace(anchor, anchor + "\n- [[Cours - coffre maître]] — collecte, archive et synchronisation du coffre de cours.\n", 1)
    arch_add = f'''\n## Coffre maître des cours\n\n- Coffre distinct : `/srv/obsidian/cours` ; partage Syncthing `obsidian-cours`, VPS sendonly vers PC receiveonly.\n- Collecte quotidienne 08:00 Europe/Bucharest et commande `/actualiser` ; export par `cours-watch-obsidian.path` puis `.service`.\n- PDFs archivés sous `/var/lib/cours-watch-publish/documents` ; preuve du {date} : {pdf['documents']} documents, {pdf['pages']} pages, archive `{arc['sha256']}`.\n- Teams : General uniquement. Synthèses manuelles datées et notes automatiques complètes sont des documents distincts.\n- Fiche : [[Cours - coffre maître]].\n'''
    arch_path = "VPS/Notes/VPS - architecture et exploitation.md"
    arch = existing(arch_path)
    if "## Coffre maître des cours" not in arch: arch += arch_add
    sync_path = "VPS/Services/Syncthing et Obsidian.md"
    sync = existing(sync_path)
    sync_add = f'''\n## Partage du coffre de cours\n\nLe dossier `/srv/obsidian/cours` est partagé sous l’identifiant `obsidian-cours`, en mode **sendonly côté VPS** et **receiveonly côté PC**. État vérifié le {date} : PC `{syn.get('pc_state','inconnu')}`, {syn.get('global_files','?')} fichiers, {syn.get('need_files','?')} fichiers à recevoir, {syn.get('pull_errors','?')} erreurs de pull. La fiche dédiée est [[Cours - coffre maître]]. Ce partage est distinct du coffre VPS `/srv/obsidian/vault` décrit ci-dessus.\n'''
    if "## Partage du coffre de cours" not in sync: sync += sync_add
    return {
        TARGET: note,
        "VPS/Accueil VPS.md": index,
        arch_path: arch,
        sync_path: sync,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="appliquer après hashguards; audit seul par défaut")
    ap.add_argument("--verification-json", type=Path, help="preuve JSON finale requise avec --apply")
    args = ap.parse_args()
    try:
        raw = run_remote(AUDIT_CODE)
        state = json.loads(raw)
        print("Audit VPS (aucune écriture) :")
        for rel, meta in state.items(): print(f"- {rel}: {meta.get('sha256', 'absent')}")
        for rel, expected in EXPECTED.items():
            if state[rel].get("sha256") != expected: raise RuntimeError(f"hashguard local périmé: {rel}")
        if state[TARGET].get("exists"): raise RuntimeError("la fiche cible existe déjà; audit manuel nécessaire")
        if not args.apply:
            print("Plan prêt; aucune modification effectuée.")
            return 0
        if not args.verification_json: raise ValueError("--apply exige --verification-json")
        proof = json.loads(args.verification_json.read_text(encoding="utf-8"))
        rendered = render(proof, state)
        payload = json.dumps({"expected": EXPECTED, "files": rendered}, ensure_ascii=False)
        result = run_remote(APPLY_CODE, payload)
        print("Application distante terminée : " + result)
        return 0
    except Exception as exc:
        print(f"ERREUR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
