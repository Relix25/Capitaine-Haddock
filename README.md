# Capitaine Haddock

Bot Discord personnel pour les cours UPB et les activités ESN Bucarest, exécuté sur un VPS. Il reste disponible lorsque le PC est éteint.

## Utilisation

| Commande | Fonction |
|---|---|
| `/a-faire [cours] [passees]` | Échéances à venir, de la plus proche à la plus lointaine ; trois cartes par message privé, pagination et liens numérotés. |
| `/cours [cours]` | Projets et échéances à venir, avec détails privés. |
| `/actualiser` | Relire Moodle et Teams, publier les changements puis afficher un point privé. |
| `/status` | Santé des sources et du bot, prochains passages, annonces acceptées par Discord et consommation du modèle. |
| `/modele [choix]` | Administrateurs : afficher le modèle actif ou choisir HY4 Flash / DeepSeek V4.1 Flash. Choix persistant, sans redémarrage. |

Les commandes exigent le rôle UPB dans le salon configuré. Leurs réponses sont éphémères et ne pingent personne. `passees: vrai` ajoute les échéances antérieures après les dates à venir, avec la mention « rendu personnel à vérifier ». Le bot ne connaît pas les dépôts ni les passages à l'oral personnels. Les dates non validées sont placées en fin de liste. Les filtres acceptés sont `tous`, `AAIT`, `AITDM`, `Big Data` et `SOS`.

Pour discuter, adresser un message à Haddock, le mentionner ou répondre à l'un de ses messages. Le fournisseur est **CommandCode**, modèle sélectionné **Tencent HY4 Flash**, avec un plafond du bot de **10 millions de tokens par jour**, remis à zéro à minuit à Bucarest. Le contexte reste court et en RAM ; le compteur conserve seulement les métadonnées de consommation.

`/modele choix:hy4` sélectionne HY4 Flash ; `/modele choix:deepseek` revient à DeepSeek V4.1 Flash. La commande exige aussi les droits administrateur, répond en privé et indique la politique ZDR. HY4 n'a pas de route ZDR disponible lors des essais : son profil n'exige pas ZDR. DeepSeek l'exige. Les deux gardent `store: false`. Le choix est enregistré sans secret dans `/var/lib/cours-watch/model-selection.json`, indépendamment de la configuration privée ; les quotas et la mémoire RAM sont conservés. Une réponse en cours termine avant le changement. Voir [le fonctionnement et le déploiement](docs/HY4-SWITCH-2026-11-17.md).

La façon de parler provient du [prompt adapté du texte fourni par l'utilisateur](courswatch/conversation/prompt-haddock.txt) : paroles seules dans le ton du capitaine, sans mimiques, gestes ou narration. [persona.py](courswatch/conversation/persona.py) ajoute le contrat technique Discord : décisions JSON, audience autorisée, secrets et faits de cours. Les préférences de langage, par exemple `/jurons rares`, se demandent dans la conversation adressée au bot ; elles n'ajoutent pas de nouvelles commandes slash Discord.

Les réponses du chatbot affichent un sous-texte avec le modèle, le TTFT, le temps total de l'appel IA, le débit de sortie estimé et les tokens d'entrée/sortie, dont le cache connu. Ces chiffres sont mesurés par le code pour chaque appel ; ils ne viennent pas du modèle et ne sont pas réinjectés dans le contexte. Une mesure absente est indiquée comme indisponible. Pour un sample vocal, Discord impose un court message de statistiques séparé en réponse à l'audio.

## Fonctions suivies

- Moodle et publications/réponses effectivement chargées dans Général des quatre équipes Teams ; Administrativ exclu.
- Nouveautés et rappels quotidiens à **08:00 Europe/Bucharest** : IMPORTANT à J-3, URGENT à J-1 et le jour même, avec un seul ping UPB par lot de rappels.
- Cartes compactes, bouton privé « Plus d'infos », traductions locales anglais/roumain → français et texte original conservé dans les détails.
- PDF versionnés par SHA256, même à nom et URL identiques, avec comptage et extraction des pages.
- Export Obsidian vers le maître `/srv/obsidian/cours`, puis copie PC Syncthing en réception seule.
- Veille du Linktree public [BucharestEvents](https://linktr.ee/BucharestEvents) toutes les cinq minutes : nouvelle URL d'activité, carte et ping ESN, référence initiale silencieuse et file persistante.
- Conversations Haddock, interventions spontanées rares et samples vocaux selon le contexte.

Les limites de couverture et les garanties de livraison figurent dans [l'architecture](docs/ARCHITECTURE.md). Le bot n'annonce pas une livraison exactement une fois et ne déduit jamais un rendu manquant d'une date passée.

## Organisation du dépôt

| Emplacement | Contenu |
|---|---|
| [`courswatch/`](courswatch/) | Implémentations du bot, collecteurs, stockage, documents, publication et conversation |
| [`tests/`](tests/) | Tests unitaires, intégration sur fixtures, migrations et exploitation |
| [`ops/`](ops/) | Construction/vérification des lots, inventaire et validation synthétique du salon |
| [`tools/run_tests.py`](tools/run_tests.py) | Profils de tests isolés |
| [`systemd/`](systemd/) | Unités et drop-ins de référence, sans deuxième copie à la racine |
| [`examples/`](examples/) | Exemples de configuration sans authentification |
| [`assets/`](assets/) | Images, voix, sources et empreintes |
| [`docs/`](docs/) | Architecture, exploitation et compte rendu CommandCode ; anciens états dans `docs/history/` |

Les vingt modules applicatifs et vingt-sept modules de tests historiques à la racine sont de petits adaptateurs, pas des implémentations dupliquées. Ils conservent les imports, les patches et les anciens chemins CLI. Les autres outils racine maintenus servent à la configuration, au diagnostic ou à la maintenance.

Les scripts ponctuels d'anciennes activations, installations, retouches de messages et rédaction de notes ont été retirés. Leurs sources restent consultables dans Git, notamment dans l'état `14f2040` précédant le nettoyage. Les anciens installateurs qui ne copiaient que les modules racine ne doivent plus servir au package actuel.

## Développer et vérifier

Python 3.12 ou ultérieur, avec les données de fuseaux horaires disponibles :

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements-bot.txt -r requirements-documents.lock tzdata
python -B tools/run_tests.py core
python -B tools/run_tests.py all
```

Le runner isole les configurations et les fichiers temporaires, retire les clés connues de l'environnement et bloque le réseau externe. Les modèles de traduction ne sont pas nécessaires aux tests simulés. Les tests POSIX et de liens symboliques doivent également être exécutés sous Linux ; un test ignoré sous Windows ne les valide pas.

Les anciens appels `python -m unittest test_course_tasks ...` restent compatibles. Employer les [profils explicites](tests/README.md) pour éviter de découvrir deux fois les adaptateurs et leurs implémentations.

Les essais réels sont séparés des tests isolés. `scan --dry-run` effectue une lecture réseau depuis une copie temporaire de la session dédiée ; il ne livre aucune notification et ne modifie ni la base, ni le rapport, ni la session d'origine. `python -m ops.validate_channel --send-test` utilise des cartes synthétiques et impose l'absence de mentions. Les autorisations de tests et la procédure GitHub sont dans [AGENTS.md](AGENTS.md).

## Exploitation et références

- [Installation, configuration et maintenance](docs/EXPLOITATION.md)
- [Préparer un lot, vérifier les modèles et revenir en arrière](docs/OPERATIONS-REFACTOR.md)
- [Basculement CommandCode, tests réels et redémarrage](docs/COMMANDCODE-2026-11-17.md)
- [Mesures TTFT DeepSeek Normal, Fast et Ultrafast](docs/benchmarks/DEEPSEEK-2026-11-17.md)
- [Évaluation de Tencent HY4 Flash comme alternative](docs/benchmarks/HY4-2026-11-17.md)
- [Premier import et origine des sources](docs/history/PREMIER-IMPORT.md)
- [Déploiement initial du package](docs/history/DEPLOYMENT-2026-11-17.md)
- [Réactivité des commandes et du chatbot](docs/history/LATENCY-2026-11-17.md)
- [Rapport de la phase locale de refactoring](docs/history/REFACTOR-2026-11-17.md)
- [Réglages documentés au 12 novembre](docs/history/Operations-historique-2026-11-12.md), [README exporté du VPS](docs/history/README-source-VPS.md) et [empreintes initiales](docs/history/source-VPS-SHA256.json)

Les documents dans `docs/history/` décrivent leurs dates respectives ; leurs anciens modèles, plafonds et blocages ne sont pas des consignes d'exploitation actuelles. Aucun historique Git antérieur au premier import du 17 novembre 2026 n'a été reconstitué.

Le déploiement actif est dans `/opt/cours-watch`, la configuration privée dans `/etc/cours-watch` et les données dans `/var/lib/cours-watch`. Les secrets, cookies, profils navigateur, bases, rapports privés, documents de cours, modèles et environnements restent hors Git. Le dépôt ne se déploie pas automatiquement : [la procédure de maintenance](docs/EXPLOITATION.md#mettre-à-jour-le-code) conserve les sauvegardes, données, compteurs et reçus.
