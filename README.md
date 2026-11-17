# Capitaine Haddock

Bot Discord personnel pour les cours UPB et les activités ESN Bucarest, exécuté sur un VPS. Il fonctionne lorsque le PC est éteint.

## Commandes

| Commande | Fonction |
|---|---|
| `/a-faire [cours] [passees]` | Prochaine échéance en premier. Trois embeds distincts par message privé, une tâche par embed, boutons de navigation et liens numérotés. Dates passées masquées par défaut. |
| `/cours [cours]` | Projets et échéances à venir, avec détails privés. |
| `/actualiser` | Relire Moodle et Teams, publier les changements puis afficher un point privé. |
| `/status` | Santé du bot et des sources, prochains passages, rappels et annonces acceptées par Discord, quota Luna et veille ESN. |

Les commandes exigent le rôle UPB dans le salon configuré. Les réponses de commandes sont éphémères et ne pingent personne. `/a-faire` commence par la date à venir la plus proche du moment de la commande. L’option `passees: vrai` ajoute les dates antérieures après les tâches à venir, de la plus proche à la plus ancienne ; elles portent « rendu personnel à vérifier ». Le bot ne connaît pas les dépôts et passages à l’oral personnels. Les dates non validées sont en fin de liste. Le filtre accepte `tous`, `AAIT`, `AITDM`, `Big Data` et `SOS`.

## Fonctions

- Moodle et publications/réponses chargées dans Général des quatre équipes Teams ; Administrativ exclu.
- Nouveautés et changements, puis rappels quotidiens à **08:00 Europe/Bucharest**. IMPORTANT à J-3, URGENT à J-1 et le jour même ; un seul ping UPB par lot de rappels.
- Embeds compacts, quatre emojis Haddock et bouton « Plus d’infos » privé.
- Traductions locales anglais/roumain → français ; texte original conservé dans les détails.
- PDFs versionnés par SHA256, même si le nom ou l’URL reste identique, avec comptage et extraction des pages.
- Export Obsidian : maître `/srv/obsidian/cours`, copie PC Syncthing en réception seule.
- Linktree public [BucharestEvents](https://linktr.ee/BucharestEvents) toutes les cinq minutes : nouvelle URL d’activité → carte et ping ESN, référence initiale silencieuse et file persistante.
- Conversations contextuelles Haddock avec Luna, interventions spontanées rares, réponses courtes et samples vocaux selon le contexte.
- Quota de **1 000 000 tokens/jour**, entrée + sortie, minuit à Bucarest. Consommation API confirmée et provisions incertaines affichées séparément.

## Code et exploitation

Le code applicatif est regroupé dans [`courswatch/`](courswatch/). Les fichiers Python historiques à la racine sont des adaptateurs : les anciens imports et chemins CLI restent utilisables et partagent les mêmes modules. Les unités relevées sur le VPS restent dans [`systemd/`](systemd/), sans modification de service pendant ce refactoring local. Les anciens scripts d’installation ponctuelle restent présents avec leurs garde-fous ; ils ne constituent pas une installation automatique universelle.

- [Architecture et fichiers](docs/ARCHITECTURE.md)
- [Installation, configuration et maintenance](docs/EXPLOITATION.md)
- [État et origine du premier import](docs/PREMIER-IMPORT.md)
- [Préparer et vérifier un lot, modèles et rollback](docs/OPERATIONS-REFACTOR.md)
- [Profils de tests isolés](tests/README.md)
- [Rapport du refactoring local du 17 novembre](docs/REFACTOR-2026-11-17.md)
- [Exemples de configuration](examples/)
- [Historique documentaire des réglages du 12 novembre](docs/Operations-historique-2026-11-12.md)
- [Empreintes du code exporté du VPS](docs/source-VPS-SHA256.json)

Le dépôt privé contient les sources, tests, unités, outils de migration et documentation. Les configurations fournies sont des exemples sans authentification. Les `.env`, cookies, profils navigateur, bases SQLite, rapports privés, documents de cours, modèles de traduction et environnements Python restent hors Git.

## Vérifier le code

Python 3.12 ou ultérieur, avec les données de fuseaux horaires disponibles :

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements-bot.txt -r requirements-documents.lock tzdata
python -B tools/run_tests.py core
python -B tools/run_tests.py all
```

Le profil `core` conserve les 63 tests de la commande historique. `all` ajoute les conversations, documents, migrations sur fixtures et régressions du pipeline. Le runner isole les fichiers temporaires et les configurations, bloque les connexions externes et n'importe aucun script smoke/probe. Les modèles de traduction ne sont pas nécessaires aux tests mockés ; leur inférence réelle reste une validation distincte dans l'environnement de traduction.

Les anciens appels `python -m unittest test_course_tasks ...` restent compatibles. Utiliser les profils explicites pour éviter de découvrir deux fois les adaptateurs et leurs implémentations. Certains tests de liens symboliques demandent Linux ou les privilèges Windows appropriés ; les tests ignorés sont comptés dans le résultat.

`scan --dry-run` effectue une vraie lecture réseau à partir d'une copie temporaire du profil dédié. Il ne modifie pas la base, le rapport ou la session d'origine et ne livre aucune notification. Ce mode n'est pas un test hors ligne ; fermer la connexion interactive et conserver le verrou de collecte. Les scripts `smoke_luna*` et `probe_luna_usage.py` restent des essais manuels payants exclus des profils.

## Historique Git

Aucun commit n’existait dans le dossier local et `/opt/cours-watch` n’était pas un dépôt Git lors du contrôle du **17 novembre 2026**. L’historique commence avec ce premier import ; les anciennes dates sont documentaires, sans commits reconstitués. Les évolutions suivantes doivent être commitées ici avant leur déploiement contrôlé.
