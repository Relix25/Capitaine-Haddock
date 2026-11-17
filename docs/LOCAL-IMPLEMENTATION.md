# Mise en œuvre locale — 17 novembre 2026

Mandat : code et documentation locaux sur `codex/conversation-memory-media`, copies et fixtures uniquement. Aucun push, merge, déploiement, migration de production, message Discord ni appel modèle réel.

## Référence et état initial

Dépôt cloné dans l'environnement sans checkout préalable. HEAD et `origin/main` à l'ouverture : `f0259d7b3325a71f42293c39fef6e85215425748`, exactement la révision auditée. Aucun changement préexistant. Aucun écart de code à réauditer ; les constats initiaux restent pertinents. Les limites « mandat d'étude uniquement » du document joint ne remplacent pas la demande actuelle d'implémentation.

Suite de référence exécutée sur une archive indépendante de ce commit : `work/venv/bin/python -B work/baseline/tools/run_tests.py all --temp-root work/baseline-tmp` : **275 tests, 0 échec, 0 erreur, 0 ignoré**, 10,943 s. Mesure de durée de tests locaux, pas de latence du bot en production.

## Propriété et ordre

Vague 1 : S migre la structure ; C et M conçoivent en lecture seule. Le coordinateur possède `main.py`, contrats et raccords Discord. Vague 2 : domaines conversation, mémoire/recherche et médias séparés. Vague 3 : profils/transport/métriques après stabilisation. Vague 4 : relecture indépendante sans écriture et corrections intégrées. Aucun fichier `brain.py`, `chat.py` ou schéma n'a plusieurs rédacteurs simultanés.

## Politique de données

La mémoire durable et les descriptions médias sont explicitement demandées. Elles remplacent la règle historique d'absence d'historique, avec les mêmes restrictions serveur/salon/rôle et sans extension d'audience. Les anciennes autorisations générales de production/publication ne s'appliquent pas à cette session.

Le journal conversationnel est séparé de `state.db` et `luna-usage.db`. Les sources métier et PDF existants restent les références. Les sources privées ne sont partagées avec un salon que par configuration explicite ; une discussion ou un OCR ne devient pas une échéance officielle.

## Validation fournisseur

Les tests de transport et vision de cette session sont simulés. Aucun support visuel fournisseur réel n'est validé ici. Les profils conservent leurs IDs API exacts, sélection, politique ZDR et quota hérités ; aucune variante `-latest`, aucun troisième modèle, aucun CLI VISION implicite.

Le propriétaire confirme pendant cette session que **les deux modèles ont la vision**. Cette déclaration porte sur les modèles. Les quatre pages publiques CommandCode tentées depuis cet environnement ont renvoyé HTTP 403 ; elles ne constituent pas une preuve négative de support. La combinaison modèle + endpoint Responses + images multiples + JSON strict + politique de conservation reste à valider réellement dans un mandat adapté. Un GIF est décodé localement en images fixes horodatées, jamais envoyé comme animation native supposée.

## Configuration et limites d'accès

Les clés historiques `discord.luna`, `model-selection.json`, `luna-usage.db` et les diagnostics restent stables. L'exemple Discord contient les paramètres de mémoire : 90 jours pour textes/descriptions, rattrapage silencieux de 7 jours et 1 000 messages maximum. Un scan partiel annonce sa couverture ; il n'établit pas l'absence de messages. Les suppressions manquées peuvent être réconciliées uniquement dans une fenêtre entièrement lue et autorisée. Les modifications hors couverture ne sont pas garanties ; les preuves conversationnelles citées sont rechargées avant publication.

`discord.luna.knowledge.allowed_sources` est une association explicite entre une clé de salon et les identifiants de sources métier déjà configurés. Exemple synthétique : `{"[\"1\",\"2\",\"\"]": ["moodle:TEST-AAIT", "teams:TEST-AAIT"]}`. Une association absente ne partage aucune source métier avec le salon. Les commandes privées existantes conservent leur propre politique. Retirer une source de cette association invalide son utilisation avant envoi.

Les médias bruts ne sont pas archivés par ce pipeline. Les occurrences et observations attribuées sont dans le journal, les descriptions intrinsèques dans `media-cache.db`, par périmètre, empreinte, profil et version de traitement. Les pages Tenor/Giphy sans média exploitable et les boucles vidéo restent explicitement non prises en charge. Aucun intent de réaction n'est ajouté. Les fichiers privés et caches ne sont pas dans les lots de code.

Les fenêtres couvertes sont des intervalles fusionnés ; deux fenêtres disjointes ne certifient pas leur trou. Le curseur reprend une page interrompue par la limite. La couverture v1 fondée seulement sur min/max est migrée conservativement en couverture inconnue, puis confirmée par une nouvelle lecture. Les auteurs des messages REST sont résolus par membre avec contrôles de rôle, sans activer l'intent membres. Les purges locales s'exécutent aussi sans nouveau succès vision : cache médias par lots bornés chaque minute, journal au démarrage et périodiquement. Les sauvegardes restent à intégrer à la politique d'exploitation.

## Comportements livrés et choix

- Les 89 Python de la racine auditée sont migrés : 42 outils rangés, 47 adaptateurs retirés, `main.py` seul Python racine. Imports, patches, CLI, argv, cwd, services de référence et manifestes sont migrés. Le lot structurel sans changement de conversation est le commit local `614a37f` ; l'inventaire détaillé est dans [STRUCTURE-MIGRATION.md](STRUCTURE-MIGRATION.md).
- Les demandes sont des tours FIFO attribués avec priorité directe. Le regroupement conserve les fragments compatibles ; deux auteurs ou deux sujets ne s'écrasent pas. Les issues répondre/attendre/silence sont explicites, avec attente réveillable et silence normal. Une réponse d'un tiers n'annule pas une question par sa seule présence : ignorance, nouvelle question ou emoji ne valent pas résolution.
- Cible, participants, liens de réponse et versions sont figés avant recherche. Corrections pertinentes : nouvelle tentative bornée ; suppression, annulation ou réponse humaine substantielle : silence. Les sources et droits sont revalidés après les derniers contrôles asynchrones, puis une validation locale finale précède l'engagement de l'envoi. Les souvenirs sociaux injectés au modèle sont eux aussi des dépendances versionnées.
- `conversation.db` conserve messages, versions, tombstones, FTS5, observations, couverture et sorties préparées/engagées/envoyées/incertaines. Les réponses du bot sont conservées sans pieds techniques ; une réponse à un ancien message du bot reste reconnue après redémarrage. Un résultat distant incertain n'est jamais renvoyé automatiquement. Les questions interrompues sont conservées, sans reprise spontanée après redémarrage.
- La recherche métier réutilise tâches, sources et PDF existants. `knowledge.db` conserve les versions effectivement observées par ce service. Les dates AAIT sont rendues avec preuves, année, précision et statut ; ambiguïté ou absence de preuve produit une clarification/limite. Une date sans heure n'acquiert pas d'heure. Une date relative est ancrée à la publication lorsqu'elle est connue, jamais à la consultation. Le rendu sélectionne des preuves et citations entières. L'aide aux commandes vient du registre actuel ; `/modele` renvoie vers `/settings`, et `/jurons` reste une préférence conversationnelle.
- Images, GIF et emojis personnalisés sont normalisés même sans texte. Le décodage PNG/GIF/WebP est réel et borné ; les images fixes des animations sont horodatées. Descriptions, OCR, animation, limites, modèle, date d'analyse et occurrence sont distincts des interprétations sociales. Le cache et les traitements en vol sont dédupliqués par contenu/périmètre/profil/version, tout en conservant chaque auteur/message/date. Un profil indisponible capturé ne se résout pas de nouveau après `/settings`.
- Les appels texte et vision utilisent un profil immuable et conservent la sélection, ZDR, `store:false` et le compteur historique. `call_profiles` est une migration additive du quota. Un 429 ou une panne commune protège tous les chemins ; une incapacité média reste locale. Les provisions incertaines restent comptées, mais une provision temporairement pleine ou trop grande ne condamne pas la journée après régularisation.
- Les phases ingestion, attente, recherche, verrou modèle, génération, validation, livraison et premier contenu utile sont instrumentées sans recopier les conversations. Les durées de jobs médias et les cache hits sont accessibles séparément de la durée murale de recherche. Le premier delta JSON reste distinct du premier contenu Discord utile. Une génération à la fois est conservée ; aucun parallélisme fournisseur supplémentaire ni changement du prompt de personnalité pour promettre un gain.

## Tests et résultats réellement exécutés

Commande finale : `work/venv/bin/python -B tools/run_tests.py all --temp-root work/final-test-tmp` : **418 tests réussis, 0 échec, 0 erreur, 0 ignoré**, en **13,108 s**. Python 3.12.14, SQLite 3.53.1, dépendances du projet et Pillow 13.0.0 dans un venv local. L'environnement et les sous-processus sont synthétiques ; les clés connues sont retirées et le réseau externe est bloqué par le runner. Les anciennes commandes, permissions, réponses éphémères, PDF, quotas, voix et mentions ont gardé leur couverture de régression.

Les nouveaux scénarios couvrent multi-auteurs/homonymes/sujets, corrections et révocations concurrentes, priorité directe, reprise J+7 après réouverture, ancienne réponse du bot hors fenêtre chaude, versions/suppressions/backfill silencieux/couverture disjointe/pagination, AAIT/report/année/date seule/sources modifiées/PDF SHA, cache et déduplication médias, OCR tardif sur frames synthétiques, profil et politique capturés malgré un changement de réglages, provisions et migrations sur copies. L'overlay tardif du GIF et les formats sont décodés avec Pillow ; les descriptions/OCR sont fournis par faux transports, jamais présentés comme une perception fournisseur réelle.

La relecture indépendante R a exécuté **199 tests isolés**, tous réussis, et n'a plus identifié de blocage d'intégrité dans les parcours relus. Elle a notamment conduit aux correctifs de revalidation finale, références médias, anciens replies, rôles REST, trous de couverture, périodes métier, rétention physique et provisions. Analyse syntaxique de l'ensemble Python, `git diff --check`, imports sans effets des outils et construction/vérification d'un lot local avec manifeste ont également réussi. Aucun service n'a été lancé pour ces vérifications.

Micro-mesure locale séparée, **1 000 messages synthétiques**, aucun appel modèle/Discord :

| Composant | Échantillons | p50 | p95 | Maximum |
| --- | ---: | ---: | ---: | ---: |
| Ingestion avec commit SQLite | 1 000 | 2,198 ms | 3,597 ms | 26,714 ms |
| Recherche FTS5 chaude, huit résultats | 100 | 2,145 ms | 2,892 ms | 3,648 ms |
| Événement dupliqué sans nouvelle version | 100 | 0,325 ms | 0,496 ms | 0,764 ms |

Ces mesures décrivent cet environnement et ce corpus. Elles ne mesurent ni la production, ni le fournisseur, ni Discord, ni un gain avant/après. Les nouveaux composants n'existaient pas dans la référence ; la durée globale des tests n'est pas une mesure de performance conversationnelle. Les optimisations implémentées sont le réveil direct, l'absence d'arbitre payant systématique, la recherche locale, les réponses factuelles locales, la déduplication et les caches ; aucun gain de production n'est revendiqué.

## Copies, migrations et rollback

Schémas nouveaux : conversation v2, cache média v1, connaissances v1. Quota : budget v2, ajout `call_profiles`, sans modification des lignes historiques `calls`. État métier, reçus, archives PDF et réglages restent à leurs emplacements. Les migrations et retours arrière ont été exercés uniquement sur des bases synthétiques/copies locales ; aucun fichier du VPS n'a été lu ou migré.

Pour une future opération autorisée : figer l'ingestion et suivre les appels déjà partis jusqu'à leur issue connue/incertaine ; sauvegarder les bases avec l'API SQLite `backup`, les réglages et les PDF de façon cohérente, puis vérifier les migrations sur copies. Ne pas copier seulement le fichier principal d'une base WAL encore ouverte. Relever consommation confirmée, provisions, reçus et empreintes avant/après.

Le code audité `f0259d7` refuse un marqueur budget v2 même si la migration est additive. **Ne jamais restaurer un ancien `luna-usage.db` après de nouveaux appels** : cela perdrait leur consommation. Le rollback doit garder le ledger courant ou préparer une copie compatible du ledger courant :

```bash
python -m ops.migration.prepare_budget_v1_copy \
  --source-copy work/current-usage-copy.db \
  --output-copy work/legacy-usage-copy.db
```

Cet outil refuse les chemins de production et l'écrasement d'un fichier existant. Il sauvegarde vers une nouvelle copie, retire seulement le marqueur `budget` v2 de la copie et garde la table de provenance ainsi que tous les appels, charges, provisions, notices et blocages. Le test charge ensuite la règle de migration v1 sur cette copie et compare les lignes. L'outil n'installe rien. L'éventuelle remise en service relève d'un autre mandat avec vérification des empreintes et des droits. Les bases conversation/médias/connaissances restent séparées et peuvent être conservées privées ou restaurées ensemble depuis une copie cohérente ; l'ancien code ne doit pas écrire dans un schéma plus récent.

## Étapes d'exploitation restantes et limites

Valider les permissions réelles du salon et de lecture historique, mapper explicitement les sources métier autorisées, adapter les appelants externes et venv/WorkingDirectory aux nouveaux chemins, contrôler les volumes/rétention/sauvegardes, puis observer les décisions sur un corpus validé. Une activation, un test de salon, une validation vision réelle ou un benchmark fournisseur/Discord nécessitent un mandat qui les couvre ; aucun n'a été effectué ici.

Les capacités réelles images + JSON + politique doivent être renseignées avec une preuve portant sur le modèle et l'endpoint exacts. Le routage vision vers l'autre profil exige une configuration explicite et une politique compatible ; il n'est jamais utilisé pour abandonner ZDR. Les métadonnées de publication absentes des anciens snapshots restent inconnues. L'historique métier ne peut reconstruire des versions antérieures jamais observées. La recherche est lexicale/structurée locale, sans embeddings ni résumés générés automatiquement. Les heuristiques de sujets et de réponse humaine restent conservatrices et la qualité conversationnelle réelle reste à évaluer.

Le transport conserve son timeout réseau et une garde de durée pendant la lecture du flux ; cela ne constitue pas une deadline globale incluant file, connexion et réponse. Une acceptation Discord n'est pas une lecture humaine. Une modification pendant un envoi déjà engagé conserve le reçu et un état `changed_during_send` à réconcilier ; aucune fausse annulation distante n'est annoncée.

## Visibilité GitHub demandée pendant la session

La connexion existante rapporte `ADMIN` et permet déjà de modifier le code. Le changement explicite vers privé a été tenté par `gh api --method PATCH repos/Relix25/Capitaine-Haddock -F private=true` : HTTP 403, `Resource not accessible by integration`. Le navigateur partagé n'a pas pu démarrer. La visibilité vérifiée reste publique ; aucun accès supplémentaire ou compte n'a été créé. Ce refus concerne la permission d'administration de l'intégration, pas l'accès local au code. Aucun push n'a été effectué.

## Corrections de la relecture du 18 novembre 2026

- `/settings` valide le modèle, la politique et le routage avant sauvegarde. Une exigence ZDR configurée s'applique aussi à HY4. Le profil d'un tour reste figé lors d'une nouvelle tentative. La vision exige une preuve explicite pour le modèle, l'API, le JSON strict et la politique. Le diagnostic utilise le même résolveur. La vérification locale de la bascule pendant un appel a utilisé de faux transports.
- Les versions et droits des occurrences utilisées sont vérifiés avant transmission puis avant publication. Une panne de lecture Discord suspend la dépendance et permet de la vérifier à nouveau. Le journal distingue la réponse REST autoritaire d'un événement rejoué au même instant. Le backfill réconcilie chaque page intégralement lue avec des bornes d'identifiants Discord.
- Le coordinateur conserve les demandes distinctes et leurs auteurs. Une précision liée pertinente peut modifier l'objectif sans changer le demandeur. Les limites de rejeu partent de la date source. La télémétrie indisponible laisse le tour en file et le premier reçu inclut le temps d'ingestion.
- La recherche borne les preuves au cours demandé, réutilise le chemin des tâches et conserve les dépendances des options de clarification. Un emoji décoratif laisse passer la recherche AAIT. « Mon GIF » prend l'auteur du tour comme filtre. Le cache conserve les occurrences distinctes et sépare date d'envoi et date d'analyse.
- Le pipeline rejette un téléchargement ou un GIF structurellement incomplet. Le cache de traitement change de version. Les observations GIF déjà mémorisées gardent leur date et signalent que leur complétude est inconnue. Une empreinte de preuve est calculée sur l'observation rendue. Un reçu vocal illisible est comptabilisé comme incertain sans nouvelle publication.
- Un total fournisseur confirmé suffit au règlement même sans ventilation. L'admission texte et vision partage un seul emplacement. La taille Base64 compte dans le plafond d'octets et le budget visuel utilise une provision par image. Les métriques d'une tentative écartée restent reliées à l'identifiant du compte d'appel, avec un dernier écart conservé séparément du dernier tour. Les corrections ne nécessitent pas de nouvelle migration.
- `/actualiser` fixe le répertoire du collecteur canonique. L'export historique inclut la ressource de personnalité requise à l'import.

La suite isolée de cette révision a exécuté **452 tests**, tous réussis, en 13,284 s. Commande : `work/venv/bin/python -B tools/run_tests.py all --temp-root work/corrections-regression-tmp`.

Comparaison du début de `on_message` jusqu'au reçu du texte, en millisecondes. Le modèle simulé prend 80 ms et chaque opération Discord simulée 20 ms. SQLite et les diagnostics sont en mémoire. La question directe seule a 14 essais par version. La question reçue 100 ms après le début d'une attente ambiante distincte en a six. Le p95 est calculé par rang entier. Chaque essai effectue une génération et un envoi.

| Scénario | f0259d7 p50 / p95 | f39e8ef p50 / p95 | Correctifs p50 / p95 |
| --- | ---: | ---: | ---: |
| Question directe seule | 251,47 / 255,03 | 315,47 / 326,20 | 336,64 / 347,91 |
| Question directe pendant l'attente ambiante | 4 004,95 / 4 005,34 | 315,77 / 317,28 | 337,46 / 349,30 |

Le GET membre redondant de la publication est supprimé. La vérification avant départ fournisseur porte le total à quatre GET dans le scénario corrigé, deux par phase. Les mesures sont des délais jusqu'au reçu simulé et les volumes d'essais sont limités. L'affichage dans le client Discord, le stockage sur disque, les capacités réelles des modèles et les gains en production restent à mesurer.
