# Architecture

## Flux

`cours-watch.timer` déclenche la collecte quotidienne. `cours_watch.py` utilise les sessions du navigateur VPS pour lire Moodle et Teams. `state.db` conserve les observations, empreintes, changements et reçus de livraison. La première collecte réussie d’une source initialise une référence silencieuse ; une erreur de source conserve les dernières données.

`course_reminders.py` rapproche les tâches configurées des sources effectivement collectées. Une date Moodle est reparsée dans le contenu courant ; les dates documentaires sont liées à l’empreinte du message ou du PDF. Une source modifiée impose une revalidation. Les rappels automatiques excluent les échéances passées ; `/a-faire` les conserve avec une indication neutre.

`discord_presentation.py` construit les embeds et détails. `course_bot.py` maintient la connexion Gateway sortante, les commandes et les boutons. `bot_ui_data.py` lie les cartes privées à leur demandeur. `course_tasks.py` lit les tâches en SQLite en lecture seule et affiche les échéances à venir de la plus proche à la plus lointaine. Chaque tâche garde son propre embed aéré : date en évidence et action séparée. `TaskListView` regroupe jusqu’à trois embeds dans le même message privé, avec un total de texte borné à 6000 caractères. Les boutons de liens portent le numéro de la tâche et sont renouvelés pour tout le groupe lors de la navigation. Les dates passées sont masquées par défaut ; si demandées, elles suivent les tâches à venir et précèdent les dates à confirmer. La pagination dure quatorze minutes et recontrôle le demandeur ainsi que l’autorisation du salon et du rôle.

`course_health.py` lit les rapports, timers et reçus ; il ne relance pas les plateformes. Le compteur de commandes est dérivé des commandes réellement déclarées dans le client.

Les embeds de tâches n’ont pas d’URL de titre : Discord ne montre que le premier embed d’un message si plusieurs partagent la même URL. Les consignes et plannings restent dans les boutons numérotés, même lorsque plusieurs tâches renvoient au même document ou à Teams. Voir la [règle officielle de déduplication](https://github.com/discord/discord-api-docs/blob/main/developers/resources/message.mdx#embed-limits).

## Modules spécialisés

| Domaine | Modules |
|---|---|
| Documents immuables | `moodle_pdf_versions.py`, `course_pdf_publish.py` |
| Collecte exportable et Obsidian | `course_capture.py`, `course_obsidian.py`, `publish_course_vault.py` |
| Traduction locale | `course_translation.py`, `translate_worker.py` |
| Personnage et quota | `luna_brain.py`, `luna_chat.py` |
| Répliques et voix | `haddock_easter_egg.py`, `haddock_voice.py`, `haddock_emojis.py` |
| Linktree ESN | `esn_watch.py`, `configure_esn.py` |

Luna utilise une courte mémoire en RAM et une base de comptage sans textes Discord. `usage.total_tokens` confirmé remplace la provision lorsque connu ; le cache n’est pas ajouté une seconde fois. L’heure de remise à zéro est celle de Bucarest. Le profil de répartie est `haddock-repartie-v3`.

Les traductions Argos/CTranslate2 sont locales et cachées séparément ; les observations originales restent intactes. L’archive de PDF distingue les octets par SHA256 et extrait le texte page par page. Le compte de collecte ne peut pas écrire directement dans le coffre : une zone de publication séparée alimente un service sous `vaultagent`.

## ESN

Le Linktree est lu anonymement avec Chrome standard. Seuls les liens actifs de la page courante sont analysés, en excluant les liens historiques de référencement, éléments masqués ou verrouillés. Les paramètres de suivi sont retirés. Une nouvelle URL canonique alimente `esn.db` et sa file durable ; renommage, réordonnancement et réapparition de la même URL ne pingent pas à nouveau.

Une modification du contenu d’un Google Form ou du seul titre d’un lien n’est pas suivie. Aucun formulaire n’est rempli. Le ping est limité au rôle ESN. Le service a `TasksMax=256` et `MemoryMax=768M` ; la limite initiale de 128 tâches bloquait Chrome et a été corrigée.

## Couverture

- Moodle : activités et dates, jusqu’à 40 discussions visibles par forum, documents directs dans la limite de taille configurée de 25 Mio.
- Teams : publications et réponses chargées dans Général des quatre équipes. Les autres canaux, fichiers non annoncés, Devoirs et Classwork ne sont pas couverts de manière exhaustive.
- Tâches : liste configurée des rappels, avec sources revalidées dans le dernier cache. Aucun suivi personnel de soumission ou de présence.
- Sessions : une expiration absolue ou MFA peut nécessiter une reconnexion. Une plateforme inaccessible reste indiquée comme telle.

## Séparation des données

Code root dans `/opt/cours-watch`, configuration privée dans `/etc/cours-watch`, données et sessions dans `/var/lib/cours-watch`. Aucun de ces états privés ne doit être importé dans Git. Le coffre maître des cours est `/srv/obsidian/cours` ; sa copie PC reste Syncthing receiveonly. Le coffre système `/srv/obsidian/vault` contient la documentation d’exploitation, distincte des cours.

## Organisation après refactoring local — 17 novembre 2026

Le package `courswatch` contient les implémentations ; les anciens fichiers à la racine restent des adaptateurs compatibles. Un import historique et son import canonique désignent le même objet module, y compris pour les patches de tests et la configuration. Le collecteur peut également être lancé par `python -m courswatch`.

| Dossier/module canonique | Responsabilité |
|---|---|
| `models.py`, `settings.py` | Observations et identités pures ; chemins partagés et commande de collecte explicite |
| `runtime.py` | Sessions dédiées, CLI, simulation isolée et bilan des étapes |
| `collectors/` | Lecture Moodle/Teams et ESN |
| `storage/` | État/outbox/reçus SQLite et migrations additives versionnées |
| `discord_app/` | Gateway, commandes, vues, transport, livraison et santé |
| `documents/` | Transfert borné, archives SHA256, correspondances de pièces jointes et publication PDF |
| `publishing/` | Snapshot pédagogique et notes automatiques |
| `conversation/` | Luna, mémoire, quota, répliques et voix |
| `translation.py`, `translation_worker.py` | Cache et worker local ; dépendances d'inférence chargées à l'utilisation |

Les modules applicatifs n'importent plus les points d'entrée historiques. Les tests sont regroupés sous `tests/` avec des adaptateurs à la racine ; `tools/run_tests.py` sélectionne les profils explicitement. `ops/` prépare des lots, des inventaires et des vérifications sans installer de service.

### État et reprise

`storage/schema.py` versionne séparément les schémas des observations, du quota et des cartes privées. Les migrations se font sous transaction ; les colonnes et tables historiques restent compatibles avec l'ancien code. L'initialisation conserve son appel depuis les constructeurs pour les anciens consommateurs, mais les transformations sont centralisées, nommées et appliquées une seule fois.

Chaque collecte réussie reçoit une génération indépendante de la précision de l'horloge. Les documents gardent leur dernier statut et leur génération de vérification. Une échéance documentaire ne devient pas fraîche sur la seule base d'une page Moodle accessible : erreur ou document non revu impose une revalidation. Les archives et les observations antérieures restent conservées ; une plateforme inaccessible garde la dernière date connue signalée comme telle.

`complete_scan` conserve l'ingestion et la file, puis distingue rappel, livraison et publication. Une panne Discord n'empêche plus le nouveau bilan ou le snapshot pédagogique. Le rapport est remplacé atomiquement et le statut global devient partiel lorsqu'une étape échoue. `/actualiser` présente explicitement une livraison en attente. La fenêtre de doublon entre acceptation distante et reçu local demeure ; aucune garantie « exactement une fois » n'est annoncée.

### Documents et mémoire

Les contextes Playwright réels utilisent un client HTTP borné avec les seuls cookies Moodle du contexte dédié, conservés en mémoire. Chaque redirection est validée ; la taille est bornée pendant le transfert et les réponses sont fermées. Les changements de cookies sont resynchronisés vers le même contexte lorsqu'il est fourni. Le transport tamponné historique subsiste seulement pour les doubles de tests sans API de session.

Les octets d'un lot de pièces jointes sont stockés temporairement dans un dossier privé ; seules les métadonnées restent en RAM entre téléchargements. L'archivage relit un PDF à la fois, sans retéléchargement. Les correspondances dans `COURSE/bindings/` fixent le lien entre l'attachement principal et l'ancien catalogue du module. Un ancien SHA doit identifier une unique pièce jointe pour créer ce lien ; sinon le catalogue historique reste figé et les nouveaux documents utilisent leurs identités propres avec avertissement. Aucun ancien PDF ou catalogue n'est effacé.

Les sessions Moodle réelles et la compatibilité des trois environnements sur Linux restent à vérifier en staging. L'outil d'audit autonome sans contexte navigateur ne resynchronise pas entre appels une éventuelle rotation de cookie ; il reste distinct du chemin de production du bot.

## Validation ultérieure sur VPS — 17 novembre 2026

Après autorisation, les trois environnements existants ont chargé le nouveau code, les deux modèles locaux ont été chargés et les 225 tests ont réussi sous Linux. Le lot applicatif `3d9e6d4` est installé, le bot est prêt et le rendu/pagination ont été vérifiés par un message synthétique dans le salon, sans mentions. La collecte manuelle Moodle/Teams reste non exécutée après refus d'approbation automatique ; le fonctionnement programmé initial a été rétabli. Voir [le compte rendu de déploiement](DEPLOYMENT-2026-11-17.md).
