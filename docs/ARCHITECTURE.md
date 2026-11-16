# Architecture

## Flux

`cours-watch.timer` déclenche la collecte quotidienne. `cours_watch.py` utilise les sessions du navigateur VPS pour lire Moodle et Teams. `state.db` conserve les observations, empreintes, changements et reçus de livraison. La première collecte réussie d’une source initialise une référence silencieuse ; une erreur de source conserve les dernières données.

`course_reminders.py` rapproche les tâches configurées des sources effectivement collectées. Une date Moodle est reparsée dans le contenu courant ; les dates documentaires sont liées à l’empreinte du message ou du PDF. Une source modifiée impose une revalidation. Les rappels automatiques excluent les échéances passées ; `/a-faire` les conserve avec une indication neutre.

`discord_presentation.py` construit les embeds et détails. `course_bot.py` maintient la connexion Gateway sortante, les commandes et les boutons. `bot_ui_data.py` lie les cartes privées à leur demandeur. `course_tasks.py` lit les tâches en SQLite en lecture seule, trie les dates par ordre décroissant et construit les pages privées. La pagination dure quatorze minutes et recontrôle le demandeur ainsi que l’autorisation du salon et du rôle.

`course_health.py` lit les rapports, timers et reçus ; il ne relance pas les plateformes. Le compteur de commandes est dérivé des commandes réellement déclarées dans le client.

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
