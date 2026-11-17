# Architecture

## Organisation du code

Les implémentations résident dans `courswatch/`. `main.py` est le seul fichier Python à la racine et démarre le bot. Les anciens adaptateurs ont été retirés après migration des imports et des patches vers les modules canoniques. Le collecteur se lance par `python -m courswatch`, les outils par `python -m ops.<dossier>.<module>`.

| Module ou dossier | Responsabilité |
|---|---|
| `models.py`, `settings.py` | Observations, identités, chemins partagés et commande de collecte explicite |
| `runtime.py` | Sessions dédiées, CLI, simulation isolée et bilan des étapes |
| `collectors/` | Lecture Moodle/Teams et Linktree ESN |
| `storage/` | Observations, outbox, reçus SQLite et migrations additives versionnées |
| `discord_app/` | Gateway, commandes, vues privées, transport, livraison et santé |
| `documents/` | Transfert borné, archives SHA256, identités de pièces jointes et publication PDF |
| `publishing/` | Snapshot pédagogique, notes automatiques et publication du coffre |
| `conversation/` | Prompt utilisateur, contrat Discord, mémoire, quota, décisions, répliques et voix |
| `translation.py`, `translation_worker.py` | Cache et worker local ; dépendances d'inférence chargées à l'utilisation |

`tests/` regroupe les implémentations ; les vingt-sept adaptateurs de tests racine conservent les anciennes commandes. `tools/run_tests.py` sélectionne les profils explicitement, sans découverte des scripts d'exploitation. `ops/` prépare des lots et vérifications. `systemd/` est l'unique copie des unités et de leurs drop-ins. Les opérations ponctuelles achevées ont été retirées de l'arbre courant et restent accessibles dans Git.

## Collecte, état et livraison

`cours-watch.timer` déclenche la collecte quotidienne. Le runtime utilise exclusivement les sessions du navigateur VPS pour lire Moodle et Teams. `state.db` conserve observations, empreintes, changements et reçus ; une première collecte réussie initialise une référence silencieuse, tandis qu'une erreur conserve les dernières données.

Chaque collecte réussie reçoit une génération indépendante de la précision de l'horloge. Les documents gardent leur statut et leur génération de vérification. Une échéance documentaire ne devient pas fraîche parce qu'une page Moodle est accessible : erreur ou document non revu impose une revalidation. Une plateforme inaccessible garde la dernière date connue, signalée comme telle.

Les rappels rapprochent les tâches configurées des sources effectivement collectées. Les dates Moodle sont reparsées dans le contenu courant ; les dates documentaires sont liées à l'empreinte du message ou du PDF. Une source modifiée impose une revalidation. Les rappels automatiques excluent les échéances passées ; `/a-faire` les affiche seulement sur demande, avec une indication neutre sur le rendu personnel.

`complete_scan` conserve l'ingestion et la file puis distingue rappel, livraison et publication. Une panne Discord n'empêche pas le nouveau bilan ou le snapshot pédagogique. Le rapport est remplacé atomiquement et le statut devient partiel lorsqu'une étape échoue ; `/actualiser` indique une livraison en attente. Une panne entre acceptation distante et reçu SQLite peut encore produire un doublon : aucune garantie « exactement une fois » n'est annoncée.

`storage/schema.py` versionne séparément les schémas des observations, du quota et des cartes privées. Les migrations sont transactionnelles et additives ; tables, colonnes et reçus historiques restent lisibles par l'ancien code. Les constructeurs gardent leur appel d'initialisation compatible, mais une migration nommée est appliquée une seule fois.

## Commandes et cartes privées

`discord_app.bot` maintient la connexion Gateway sortante, les quatre commandes et les boutons. Les contrôles imposent serveur, salon et rôle configurés ; une carte privée est liée à son demandeur.

`TaskListView` regroupe jusqu'à trois embeds distincts par message privé, dans la limite totale de 6 000 caractères. Chaque tâche garde sa date visible, son action séparée et des boutons de liens numérotés. Les dates à venir sont croissantes ; les dates passées optionnelles suivent de la plus proche à la plus ancienne ; les dates à confirmer terminent la liste. La pagination dure quatorze minutes et recontrôle l'accès et le demandeur.

Les embeds de tâches n'ont pas d'URL de titre : Discord ne montre que le premier embed lorsque plusieurs partagent cette URL. Les consignes et plannings restent dans les boutons, même lorsque plusieurs tâches renvoient au même document. Voir la [règle officielle de déduplication](https://github.com/discord/discord-api-docs/blob/main/developers/resources/message.mdx#embed-limits).

Les commandes sur cache utilisent directement la réponse initiale privée lorsque la préparation est rapide. Au-delà de 500 ms, un différé privé acquitte la demande. `discord_app.health` lit les rapports, timers et reçus sans relancer les plateformes ; le nombre de commandes est dérivé du client réellement déclaré.

## Conversations et modèle

La configuration active sélectionne CommandCode et `deepseek/deepseek-v4.1-flash`. L'endpoint Responses est fixe et la clé `CMD_API_KEY` est lue dans le fichier privé du VPS. Une erreur ne provoque aucun repli automatique vers un autre fournisseur. Les requêtes imposent `store: false` et `x-cmd-zdr: 1` ; la disponibilité d'une route sans rétention reste celle du fournisseur.

`conversation/prompt-haddock.txt` adapte le texte fourni par l'utilisateur à une conversation composée uniquement de paroles : ton et vocabulaire du capitaine, sans narration de gestes ni mise en scène. `conversation/persona.py` le charge et ajoute le contrat technique : décision JSON validée, audience Discord autorisée, absence de secrets, d'actions exécutables et de faits de cours inventés. Les préférences de langage, dont `/jurons`, s'expriment dans les messages adressés au bot ; elles n'enregistrent pas de commandes slash supplémentaires.

La mémoire de conversation est courte et en RAM ; aucun historique de textes Discord n'est conservé dans SQLite. Le regroupement des demandes directes attend 150 ms ; les interventions spontanées gardent leurs limites propres et le silence par défaut. Un indicateur de saisie accompagne les demandes directes. Les samples vocaux ne remplacent pas une réponse utile à une question.

Les appels de conversation demandent un flux Responses. `conversation.metrics` mesure le premier delta de texte non blanc et l'événement terminal, puis conserve les mesures séparément des trois champs de décision JSON. Le modèle ne calcule pas ses propres statistiques. Le client attend toujours une décision complète et validée avant d'envoyer la réponse.

Le sous-texte des seules réponses du chatbot utilise le format Discord `-# `. `Total IA` couvre l'appel réseau jusqu'à la réponse terminale, hors attente locale et livraison Discord. Le TTFT couvre l'appel jusqu'au premier delta textuel ; ce delta peut commencer l'enveloppe JSON. Les TPS sont une estimation après ce delta : `(tokens de sortie - tokens de raisonnement connus - 1) / (Total IA - TTFT)`, en secondes. Ils restent indisponibles si le flux, les compteurs ou un intervalle suffisant manquent. Les tokens totaux suivent la valeur API, sinon entrée + sortie lorsqu'elles sont connues ; le cache est une partie de l'entrée.

Le texte principal et les statistiques tiennent ensemble dans 2 000 caractères et les statistiques restent hors des blocs de code. Une réponse locale sans appel modèle affiche zéro token ; une consommation incertaine reste signalée comme non confirmée. Les [messages vocaux natifs Discord](https://github.com/discord/discord-api-docs/blob/main/developers/resources/message.mdx#voice-messages) refusent le contenu texte et l'édition : un sous-texte séparé répond donc à l'audio, sans mention. Un échec de ce complément ne provoque pas un deuxième envoi du vocal. La mémoire conserve seulement la réplique ou le marqueur vocal, jamais le sous-texte.

Le plafond quotidien du bot est de 10 millions de tokens, remis à zéro à minuit à Bucarest, et ne change pas les limites de l'abonnement. `usage.total_tokens` confirmé remplace la provision quand il est connu ; les tokens en cache ne sont pas ajoutés une seconde fois. Les réservations incertaines restent comptées dans la protection du quota sans être présentées comme une consommation confirmée. Changer de fournisseur ou de plafond ne réinitialise pas le compteur.

## Documents, traduction et coffre

Les contextes Playwright réels utilisent un client HTTP borné avec les seuls cookies Moodle du contexte dédié, gardés en mémoire. Chaque redirection est validée, la taille est bornée pendant le transfert et les réponses sont fermées. Les changements de cookies sont resynchronisés vers le même contexte lorsqu'il est fourni ; le transport tamponné ne subsiste que pour les doubles sans API de session.

Les octets d'un lot sont temporairement stockés dans un dossier privé ; seules les métadonnées restent en RAM entre téléchargements. L'archivage relit un PDF à la fois, sans retéléchargement. Les correspondances dans `COURSE/bindings/` fixent le lien entre l'attachement principal et l'ancien catalogue du module. Un ancien SHA doit identifier une unique pièce jointe pour créer ce lien ; sinon l'ancien catalogue reste figé et les nouveaux documents utilisent leurs identités propres avec avertissement. Aucun ancien PDF ou catalogue n'est effacé.

Les traductions Argos/CTranslate2 sont locales et cachées séparément ; les observations et PDF originaux restent intacts. Le compte de collecte ne peut pas écrire directement dans le coffre : une zone de publication séparée alimente un service sous `vaultagent`.

## Veille ESN

Le Linktree public est lu anonymement avec Chrome standard. Seuls les liens actifs de la page courante sont analysés, hors références historiques, éléments masqués ou verrouillés. Les paramètres de suivi sont retirés. Une nouvelle URL canonique alimente `esn.db` et sa file durable ; renommage, réordonnancement et réapparition de la même URL ne pingent pas à nouveau.

Une modification du contenu d'un Google Form ou du seul titre d'un lien n'est pas suivie. Aucun formulaire n'est rempli. Les annonces sont limitées au rôle ESN configuré. Les contraintes de service et les drop-ins restent dans `systemd/`.

## Limites de couverture

- Moodle : activités et dates, jusqu'à 40 discussions visibles par forum, documents directs dans la limite de taille configurée de 25 Mio.
- Teams : publications et réponses chargées dans Général des quatre équipes. Autres canaux, fichiers non annoncés, Devoirs et Classwork ne sont pas couverts exhaustivement.
- Tâches : liste configurée des rappels, avec sources revalidées dans le dernier cache ; aucun suivi personnel de soumission ou de présence.
- Sessions : expiration absolue ou MFA peut nécessiter une reconnexion. Une plateforme inaccessible reste indiquée comme telle.

Le client d'audit PDF autonome ne resynchronise pas entre appels une éventuelle rotation de cookie ; il reste distinct du chemin de production avec contexte navigateur. La nouvelle génération de collecte manuelle Moodle/Teams n'a pas été déclarée validée par les tests du salon. Les limites des vérifications réellement exécutées restent dans les [comptes rendus datés](history/DEPLOYMENT-2026-11-17.md).

## Séparation des données

Code root dans `/opt/cours-watch`, configuration privée dans `/etc/cours-watch`, données et sessions dans `/var/lib/cours-watch`. Aucun de ces états privés n'est importé dans Git. Le coffre maître est `/srv/obsidian/cours` ; sa copie PC reste Syncthing en réception seule. `/srv/obsidian/vault` contient la documentation d'exploitation, distincte des cours.
