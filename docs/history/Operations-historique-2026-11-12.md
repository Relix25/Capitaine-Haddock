# Veille des cours UPB sur le VPS

> Note locale du 17 novembre 2026 : ce document décrit un état antérieur. La demande explicite autorise maintenant un journal durable du salon autorisé et les descriptions médias ; voir [LOCAL-IMPLEMENTATION.md](../LOCAL-IMPLEMENTATION.md). Les autres restrictions restent applicables. Aucun déploiement de ce nouveau code n’a été effectué.

Ce service consulte Moodle et Teams avec un navigateur dédié, compare les informations collectées aux dernières observations et envoie des alertes Discord ainsi qu'un rappel quotidien des projets. Il fonctionne sur le VPS lorsque le PC est éteint. Le cours Administrativ est exclu.

## État au 12 novembre 2026

- Environnement Python, Playwright et Chrome stable installés ; le sandbox Chrome est conservé et a été testé sous les restrictions du service.
- 88 tests couvrent les références, rappels, accès privé, traductions, voix et conversations Luna, dont les quotas persistants, le cache, les réponses API incomplètes/refusées et la reprise sans blocage du salon.
- `cours-watch.timer` est programmé chaque jour à **08:00 Europe/Bucharest** ; systemd gère le passage été/hiver et rattrape une échéance manquée.
- Les huit sources Moodle/Teams ont été lues avec les sessions du VPS. Le fichier `/etc/cours-watch/ready` active la collecte quotidienne. Le webhook Capitaine Haddock et le rôle UPB sont configurés et testés.
- Aucun cookie de la session locale n'a été exporté ni transféré. Le navigateur intégré n'expose pas cette fonction dans ses capacités disponibles.
- Les alertes de perte de session Moodle de la mise en route ont été suivies de leur rétablissement après correction de la persistance. La première référence des contenus est silencieuse.
- `cours-watch-session.timer` maintient Moodle actif avec une lecture HTTP légère toutes les vingt minutes, sans navigateur et sans notification de contenu.
- Les navigateurs disposent de 3 Gio maximum. Un verrou partagé évite les accès simultanés au profil ; le verrou de connexion est nettoyé même après un arrêt anormal.
- Le premier point réel sur les dix échéances a été envoyé en quatre messages, avec un seul ping UPB. Un second déclenchement n'a envoyé aucun doublon ; le rappel de 08:00 reste prévu.
- Les quatre cours disposent de cartes interactives avec embeds compacts. L'application `1510767148006641866` est active : `/cours`, `/actualiser`, `/status` et boutons privés. Les boutons et premières commandes ont été confirmés par l'utilisateur ; les trois commandes sont enregistrées dans l'API Discord. Les quatre emojis Haddock du pack fourni sont ajoutés au serveur et appliqués aux cartes existantes sans nouvelle publication.

## Conversations Luna et quota quotidien

Le bot utilise `gpt-6-luna` pour discuter avec le ton de Haddock lorsqu’on le mentionne, lui répond ou écrit « Haddock, … ». Il peut intervenir dans les conversations entre camarades, avec silence par défaut et au plus quatre interventions spontanées par heure. Il garde un contexte court en RAM (30 messages, 30 minutes, environ 18 Ko), envoyé à OpenAI lors des appels, sans historique local de conversation. `store: false` est utilisé pour Responses. Les anciens mots déclencheurs courts restent acceptés ; les samples passent à 50 % seulement lorsque leur thème convient.

Profil actif `haddock-repartie-v3` : une phrase brève pour le chambrage, généralement 8 à 20 mots, une seule pique et aucune métaphore par défaut. Les insultes adressées au capitaine pendant une joute appellent une contre-pique ; une formule familière isolée ne déclenche plus automatiquement un discours de soutien. Une demande explicite d’arrêt et une confidence sérieuse restent distinguées du chambrage. Sept appels réels au total : cinq tours du fil de la capture et deux contrôles ont validé les répliques courtes, l’arrêt explicite et le contexte sérieux. Les évaluations API sont comptées dans le quota. Le profil est visible dans les rapports ; `smoke_luna_repartie.py` est un outil manuel payant, pas un test automatique.

**Plafond : 1 000 000 de tokens par jour, entrée + sortie, reset à minuit Europe/Bucharest.** Toutes les analyses comptent, même si Haddock décide de se taire. Le compteur SQLite `/var/lib/cours-watch/luna-usage.db` survit aux redémarrages. Une réservation conservatrice avant l’appel prévient les dépassements ; en cas de timeout, elle reste comptée. Le blocage peut donc arriver légèrement avant un million. Aucun plafond monétaire distinct. Après blocage, aucun nouvel appel jusqu’au lendemain : les sollicitations reçoivent « Mille sabords ! J’ai englouti ma ration de mots pour aujourd’hui. Le capitaine est en cale sèche jusqu’à minuit, heure de Bucarest ! »

`/status` affiche le modèle, la disponibilité et le compteur du jour, sans API. Les commandes, rappels et traductions locales restent disponibles après épuisement. La clé `OPENAI_API_KEY` est lue dans le `.env` privé du VPS sans export vers les processus de navigateur ou de traduction. `discord.luna` dans `config.json` contient `enabled: true`, `model: gpt-6-luna`, `daily_token_limit: 1000000`. Désactiver cette section et redémarrer le client rétablit l’easter egg local configuré. Ne pas supprimer le compteur pour réinitialiser le quota.

L’affichage distingue désormais les **tokens confirmés par l’API** (`usage.total_tokens`, sinon entrée + sortie) et les **réservations non confirmées**. Le cache fait partie de l’entrée et n’est pas ajouté une seconde fois. Les provisions d’un appel en cours ou incertain restent dans le calcul de protection du quota, sans être présentées comme des tokens déjà consommés. `used_tokens` est le total confirmé ; `reserved_tokens` la provision ; `budget_used_tokens` leur somme. La migration conserve l’historique et ajoute les métadonnées de total API/cache pour les futurs appels. Contrôle réel : 1 621 en entrée, dont 1 568 en cache, + 42 en sortie = 1 663, identique à `total_tokens` et à l’incrément local. Voir la [documentation officielle des tokens](https://developers.openai.com/api/docs/guides/token-counting).

Les 88 tests passent, avec un quota simulé pour éprouver le blocage sans dépenser un million de tokens. Le premier test API de trois appels a validé question directe, contexte et silence sur un échange banal, pour 2 482 tokens. Client connecté et compteur prévisualisé dans `/status`. La procédure détaillée et les limites sont aussi dans la fiche Obsidian du VPS et sa note d’architecture. `smoke_luna.py` effectue des appels payants explicites comptés dans le vrai quota ; ce n’est pas un test à relancer automatiquement.

Un incident `invalid_response` a révélé que toute réponse inexploitable provoquait la même pause globale de 60 secondes qu’une panne réseau. Le traitement distingue désormais refus du modèle, sortie incomplète, JSON invalide et schéma invalide : une erreur propre à un message donne un bref retour local, sans empêcher l’appel suivant. Les vrais problèmes API/réseau conservent leur temporisation. Aucun refus n’est réessayé automatiquement. Les tokens réellement utilisés restent comptés, même quand la réponse est rejetée. `/status` distingue la dernière réponse inexploitable d’une panne de liaison. Le rejeu diagnostique du message initial a réussi ; l’ancien rapport ne permet pas de préciser rétroactivement le type exact de la première réponse invalide. `diagnose_luna_failure.py` ne publie rien dans Discord ; `--probe` transmet un contexte borné à OpenAI pour un seul appel facturé dans le quota, sans enregistrer les textes ni remplacer le rapport de santé du client.

## Chemins du serveur

| Objet | Chemin |
|---|---|
| Code et environnement, administrés par root | `/opt/cours-watch/` |
| Versions des dépendances installées | `/opt/cours-watch/requirements.lock` |
| Configuration sans jeton | `/etc/cours-watch/config.json` |
| Secrets Discord, root:courswatch, mode 0640 | `/etc/cours-watch/secrets.json` |
| Jeton Bot saisi par l'utilisateur, root:courswatch, mode 0640 | `/etc/cours-watch/.env`, champ `DISCORD_BOT_TOKEN` |
| Profil navigateur, base SQLite et rapports | `/var/lib/cours-watch/`, utilisateur isolé `courswatch`, mode 0700 |
| Cookies Moodle du seul navigateur VPS, mode 0600 | `/var/lib/cours-watch/moodle-cookies.json` |
| Dernier bilan | `/var/lib/cours-watch/reports/latest.json` |
| Contrôle léger Moodle | `/var/lib/cours-watch/reports/moodle-session.json` |
| Règles et échéances des rappels, sans secret | `/etc/cours-watch/reminders.json`, root:courswatch, mode 0640 |
| Dernier déclenchement de récapitulatif | `/var/lib/cours-watch/reports/latest-reminders.json` |
| Unités de service | `cours-watch.service`, `cours-watch.timer`, `cours-watch-session.service`, `cours-watch-session.timer`, `cours-watch-login.service` |

Le compte `courswatch` n'a ni sudo, ni accès aux clés SSH, au Docker socket, aux comptes d'autres applications ou au coffre Obsidian. Le code est en lecture seule pour lui. Les cookies/jetons du profil ne sont jamais placés dans Git ou Obsidian.

## Couverture et alertes

- Moodle : activités, dates de devoirs, discussions et posts des 40 premières discussions visibles par forum ; empreinte des documents directs jusqu'à 25 Mio, ou métadonnées disponibles. Certaines ressources encapsulées et les discussions au-delà de cette limite peuvent demander une adaptation.
- Teams : publications et réponses effectivement chargées dans Général des quatre équipes configurées. Les fichiers non annoncés, les autres canaux, Devoirs et Classwork ne sont pas couverts. La navigation et les sélecteurs ont été validés dans la session du VPS.
- Première collecte réussie par source : création silencieuse d'une référence. Ensuite, alerte pour nouvel élément ou modification. Aucun faux retrait n'est déduit d'un élément non chargé dans une liste virtualisée.
- Une panne ou session expirée est signalée comme telle ; elle ne remplace pas la référence par une liste vide. Une erreur identique ne génère pas chaque jour une nouvelle alerte ; le rétablissement est signalé.
- Les mentions sont limitées aux identifiants configurés. Les textes provenant des sites ne peuvent pas ajouter de ping `@everyone`.
- Les liens d'un même fil Moodle sont regroupés par identifiant de discussion ; les paramètres `parent` et ancres de réponse ne créent pas de fausse modification. Les paragraphes et listes chargés sur Teams sont lus.
- Les fichiers Moodle sont lus par GET, car HEAD échoue sur ce serveur. L'empreinte initiale d'un document déjà connu est silencieuse ; un changement ultérieur déclenche une alerte. La réponse est libérée après traitement.
- La file SQLite conserve les alertes tant que leur envoi n'est pas confirmé. Un timeout après acceptation distante peut exceptionnellement entraîner un doublon lors de la reprise : les API Discord ne fournissent pas ici une transaction commune avec SQLite.

## Rappels des projets

À 08:00 Europe/Bucharest, un point sur les projets et rendus à venir est envoyé même sans nouveauté. Les règles sont dans `reminders.json`, champ `instructions` et valeurs `policy` : **IMPORTANT à J-3**, **URGENT à J-1 ou le jour même**. Un seul message du lot mentionne UPB ; les autres gardent les dates, actions et sources sans nouvelle mention.

Les échéances Moodle sont relues dans les données de chaque collecte. Les dates publiées dans un PDF ou une annonce conservent leur source et son empreinte ; si cette source change, la date est marquée à confirmer. Aucune heure n'est inventée pour une date seule. Les horaires Moodle sont repris tels qu'affichés. Les échéances passées sont exclues des rappels, sans présumer d'un retard personnel ; `completed: true` permet de retirer une tâche lorsque l'utilisateur le demande.

Les clés de file `daily-reminder:DATE:COURS:PARTIE` empêchent un second envoi dans la journée. Avant 08:00, une collecte manuelle ne consomme pas le rappel prévu. `cours_watch.py brief` publie un premier point immédiat, avec ses propres clés, et reste sans doublon si relancé le même jour. `cours_watch.py remind` traite le rappel et la file sans nouvelle navigation.

SQLite conserve les reçus sans secret dans `discord_receipts` : identifiant du message, salon et rôles mentionnés. Le récapitulatif initial est [consultable dans Discord](https://discord.com/channels/1376984512046563519/1570209088929800282/1570223694423392307).

## Configuration Discord

Configuration retenue : webhook **Capitaine Haddock**, rôle **UPB** `1570209160744673411`, salon `1570209088929800282` du serveur `1376984512046563519`. Le jeton reste uniquement dans le fichier secret du VPS. `persona: haddock` ajoute des expressions nautiques sans changer les consignes, dates ou liens. Le message de test `1570211962342613003` a été confirmé par Discord avec le rôle dans `mention_roles`. Le reçu non secret est `/etc/cours-watch/discord-test.json`. Les quatre comptes doivent posséder ce rôle pour recevoir ses mentions.

Trois modes sont implémentés :

1. `webhook` : salon privé de serveur, `DISCORD_WEBHOOK_URL` dans le fichier secret et rôle ou utilisateurs mentionnés dans la configuration.
2. `individual_dm` : quatre destinataires dans `user_ids` et `DISCORD_BOT_TOKEN` dans le fichier secret. Chaque personne doit avoir accepté les messages du bot ; une erreur de permission reste visible.
3. `bot_channel` : application Discord installée dans le serveur, embeds avec bouton « Plus d’infos » et écoute sortante Gateway. Aucun port public supplémentaire. Le basculement se fait après validation avec `activate_bot.py` ; le webhook reste actif tant que cette validation n'a pas abouti.

Les applications utilisables dans un DM de groupe peuvent répondre à une interaction ; ce mécanisme ne fournit pas un canal d'envoi programmé libre pour cette veille. Le mode groupe DM n'est pas annoncé comme fonctionnel.

## Embeds et commandes privées

Les embeds affichent le cours, les échéances et l'urgence ; les consignes longues sont conservées dans la base pour les détails. Avec le webhook seul, les liens de source restent dans la carte. Les boutons interactifs exigent l'application Discord.

Le client actif, `course_bot.py`, utilise `discord.py 2.7.1` dans `/opt/cours-watch/bot-venv`. Guilds, Guild Messages et Message Content sont activés pour l'easter egg après autorisation explicite de l'utilisateur ; Members et Presence restent désactivés. Il ajoute uniquement `/cours`, `/actualiser` et `/status` dans le serveur configuré, en préservant les autres commandes de l'application. `cours-watch-bot.service` est actif et activé au démarrage du VPS.

- `/cours` affiche le dernier point en privé et accepte un filtre de cours.
- `/actualiser` relit Moodle et Teams puis affiche le point en privé. Les nouvelles informations suivent le canal de notification habituel ; les collectes concurrentes sont évitées.
- `/status` affiche en privé la connexion et la latence du bot, les huit sources, la dernière collecte, le maintien de session Moodle, le prochain passage réel du timer, le rappel du jour et les trois derniers lots d'annonces avec leurs confirmations et liens. Il indique les envois en attente et les échecs ou contrôles trop anciens, sans lancer de collecte ni envoyer de ping.
- « Plus d’infos » répond de façon éphémère, visible uniquement par la personne qui clique. L'accès exige le serveur, le salon et le rôle UPB configurés. Une carte privée reste aussi liée à son demandeur.

Le `.env` se modifie avec `sudo nano /etc/cours-watch/.env`, ligne `DISCORD_BOT_TOKEN=...`. Il n'est jamais placé dans Git ou Obsidian. La validation est `sudo /opt/cours-watch/venv/bin/python /opt/cours-watch/configure_bot.py 1510767148006641866 --from-env`. Le jeton n'est pas affiché. `activate_bot.py` démarre l'écoute, vérifie les commandes et les droits, puis sélectionne `bot_channel` uniquement si tout est prêt. Le service reste derrière le marqueur `/etc/cours-watch/bot-ready`.

Les reçus publics sont dans `discord_receipts` ; les cartes privées sont liées à leur auteur dans `ui_messages`. L'état d'écoute est `/var/lib/cours-watch/reports/bot-status.json`. Les boutons publics sont enregistrés de façon persistante pour survivre aux redémarrages du client.

`course_health.py` lit SQLite et les rapports, puis interroge systemd sans privilège pour connaître les vrais états et horaires. Avant 08:00, le rappel est « pas encore dû » ; après 08:20, un rappel attendu sans confirmation est signalé, sauf si une collecte est en cours. Un timer arrêté n'est pas présenté comme un prochain envoi actif. « Confirmé » signifie accepté par Discord, sans suivi de lecture des personnes. `preview_status.py` permet un aperçu en lecture seule sur le serveur. Les actualisations manuelles enregistrent leur résultat et une reprise Gateway rétablit correctement l'état de connexion.

### Emojis Haddock

Les quatre PNG d'origine sont conservés dans `assets/haddock/`. `discord_emoji_admin.py` importe le pack de façon idempotente et enregistre `/etc/cours-watch/emojis.json` (identifiants et empreintes, aucun secret). `haddock_emojis.py` valide les références avant usage ; en l'absence du fichier, les emojis ordinaires servent de secours.

- `haddock_longue_vue_projet` : projets et bouton « Plus d’infos ».
- `haddock_idee_oral` : oraux et présentations.
- `haddock_joie_depot` : service connecté, confirmations et accès rétabli.
- `haddock_mille_sabords` : urgences, erreurs et dates à confirmer.

Les références personnalisées sont placées dans les descriptions, valeurs de champs et boutons ; les titres restent en texte. Une petite vignette utilise la même image. Les couleurs, mentions IMPORTANT/URGENT, dates, sources et droits des détails privés sont conservés. Le contrôle réel du 12 novembre confirme les quatre emojis du serveur et les quatre cartes interactives actualisées ; les quatre anciens messages sans embed sont conservés. Aucun nouveau message ni ping n'a été ajouté pour cette mise à jour.

### Traduction des messages en français

Les nouvelles publications et réponses Teams, ainsi que les messages de forum Moodle, passent par `course_translation.py` avant l'envoi. Les fichiers, empreintes de documents, rappels déjà en français et cartes système de réunion ne sont pas traduits. Les collecteurs ne disposent pas d'une attribution fiable enseignant/étudiant : les réponses recueillies sont donc incluses, sans les attribuer automatiquement à un professeur.

La traduction fonctionne **localement sur le CPU du VPS**, sans clé d'API : CTranslate2 4.9.0 et modèles Argos 1.9 anglais→français, roumain→anglais→français. L'environnement séparé `/opt/cours-watch/translation-venv` et les modèles `/opt/cours-watch/models/` appartiennent à root. Les versions sont dans `requirements-translation.lock` et les sources/empreintes dans `models/manifest.json`. Le processus de traduction démarre à la demande puis s'arrête ; aucun nouveau port n'est ouvert. Les textes ne sont pas envoyés à un fournisseur de traduction externe.

`/etc/cours-watch/translation.json` active le traitement ; `/var/lib/cours-watch/translations.db` conserve les résultats par empreinte du titre et du texte d'origine, avec la version du moteur. Les `items`, leurs empreintes et les contenus de `outbox` restent originaux. Un message modifié est retraduit ; les traductions en échec sont réessayées. Une panne ne bloque pas la notification : le texte original porte alors la mention « Traduction indisponible ». Les textes déjà reconnus comme français sont conservés.

Les horaires AM/PM, URL, noms de fichiers, options et commandes sont protégés ; un changement détecté des nombres ou éléments protégés fait rejeter la traduction. Les embeds présentent le français ; « Plus d’infos » contient la traduction, l'original et la source. Le caractère automatique reste indiqué, et les consignes importantes déjà présentes ont été relues. La traduction reste une aide : l'original fait référence, notamment pour une formulation ambiguë ou une langue hors anglais/roumain/français.

Validation réelle du 12 novembre : **28 messages recueillis traduits**, dont un en roumain ; six traductions importantes relues (consignes Crafter, phase 1 AITDM et SOS, notamment). Quatre cartes d'archives, une par cours, ont été acceptées par Discord sans mention de rôle ; un second lancement n'a rien renvoyé. Trois anciennes notifications disposant d'un reçu ont aussi été actualisées. Les archives indiquent les dates des messages pour éviter de prendre un ancien « aujourd'hui » pour une annonce actuelle. [Archives AAIT](https://discord.com/channels/1376984512046563519/1570209088929800282/1570246844775399636).

Outils : `backfill_translations.py` prépare les traductions sans publier ; `--publish` publie uniquement le lot d'archives à clés stables. `review_existing_translations.py` applique les six formulations relues uniquement si les sources correspondent au relevé. `upgrade_translated_messages.py` modifie seulement les notifications dont la propriété et le salon sont vérifiés. Le bilan est `/var/lib/cours-watch/reports/translation.json` ; le relevé privé détaillé est `reports/translations-backfill.json`. Ne pas recopier cookies ou jetons avec ces rapports.

Validation réelle du 12 novembre : application authentifiée sous le nom Capitaine Haddock, commandes retrouvées via l'API, embeds et composants acceptés, accès au salon et mention UPB autorisés. Un clic réel a déclenché une réponse privée et l'utilisateur a confirmé le fonctionnement des commandes. [Carte SOS interactive](https://discord.com/channels/1376984512046563519/1570209088929800282/1570230953404665994). Le transport courant est `bot_channel`, avec `presentation: embed` et `interactive: true` ; le webhook existant est conservé comme configuration de secours.

## Easter egg Haddock

Dans le salon privé configuré, un membre UPB qui écrit « Haddock », « Tintin », « Moulinsart », « mille sabords » ou un autre terme du module `haddock_easter_egg.py` obtient une courte réplique aléatoire avec l'emoji Haddock. Une mention du bot fonctionne aussi. Les accents, apostrophes et majuscules sont normalisés ; les fragments de mots ne déclenchent pas de réponse.

Huit exclamations courtes, sans répétition immédiate ; **2 secondes minimum entre deux réponses dans le salon**. Les messages des bots, webhooks, autres salons, DM et personnes sans le rôle UPB sont ignorés. La réponse n'ajoute aucun ping et ne déclenche pas de collecte.

Configuration : `discord.easter_egg.enabled` et `cooldown_seconds` dans `/etc/cours-watch/config.json`. La fonction est désactivée par défaut si cette section est absente. Le 12 novembre, l'utilisateur a explicitement autorisé l'activation du Message Content Intent. Discord peut ainsi transmettre les contenus des salons visibles par le bot ; le code les filtre sur le serveur/salon/rôle configurés. Le cache de messages de discord.py est désactivé (`max_messages=None`) et aucun texte ou auteur déclencheur n'est écrit en base ou dans les journaux. Seuls l'heure et l'identifiant de la réponse peuvent apparaître dans `reports/bot-status.json`.

Validation : 55 tests passent, notamment un test utilisant la vraie méthode `discord.Message.reply` avec validation des paramètres de `Messageable.send`. Le 12 novembre à 03:42, une reprise ciblée du vrai message de test de l'utilisateur a produit une [réponse confirmée par Discord](https://discord.com/channels/1376984512046563519/1570209088929800282/1570252440081530974), puis l'écoute normale a repris. L'appel initial transmettait à tort `fail_if_not_exists` à `reply` ; ce paramètre a été retiré. `on_error` conserve désormais seulement le nom du callback et le type d'erreur, sans texte d'exception ni contenu du message. Pour suspendre la fonction, mettre `enabled` à `false` puis redémarrer `cours-watch-bot.service`.

### Répliques vocales

L'utilisateur a choisi **50 % de chances d'obtenir un vocal** à chaque déclenchement ; les autres réponses restent en texte. Le délai de 2 secondes s'applique aux deux formats. Un échantillon vocal n'est pas répété immédiatement ; si son envoi échoue, le bot revient au texte.

Cinq courts extraits existants de Haddock, entre **2,8 et 10,1 secondes**, ont été récupérés depuis les pages publiques de [ZoneSons](https://zonesons.com/capitaine-haddock), puis convertis en Opus mono 48 kHz. Ce sont des extraits enregistrés, sans clonage ni synthèse de voix. Sources exactes et empreintes dans `assets/haddock-audio/manifest.json`. Le VPS utilise la copie root `/opt/cours-watch/assets/haddock-audio/` ; le bot valide les chemins, formats, tailles et SHA256 avant usage. Aucune récupération externe n'a lieu lors d'un déclenchement.

L'envoi utilise le véritable format de message vocal Discord : un seul fichier OGG/Opus, drapeau `IS_VOICE_MESSAGE`, durée mesurée et forme d'onde calculée. Pas de texte/embeds ajouté au même message, pas de ping. Le nonce limite les doublons en cas de reprise. Les permissions audio étaient déjà présentes ; aucun nouveau droit n'a été accordé. La version discord.py ne fournissant pas directement cet envoi, `haddock_voice.py` utilise l'API multipart documentée.

Un [vocal de test réel](https://discord.com/channels/1376984512046563519/1570209088929800282/1570255153528049674) a été accepté comme natif avec durée et forme d'onde ; reçu sans secret dans `/etc/cours-watch/voice-demo.json`. Le client est prêt avec cinq samples et `voice_probability: 0.5`. Les 63 tests couvrent aussi la sélection audio/texte, les erreurs, les chemins invalides et les métadonnées du fichier.

Paramètre : `discord.easter_egg.voice_probability` dans `/etc/cours-watch/config.json` (`0` = texte, `0.5` = moitié au hasard, `1` = vocal si disponible), puis redémarrer le bot. `prepare_haddock_audio.py` prépare les sources dans le dossier privé `audio-staging` ; installer ensuite les fichiers validés sous root avant activation. L'encodeur isolé vient de `imageio-ffmpeg 0.8.1` dans le bot-venv ; aucune dépendance à un binaire ffmpeg système ou à un GPU. La première tentative d'installation système a été arrêtée pendant le téléchargement ; les autres services ne sont pas modifiés.

## Authentification et import de cookies


`import-cookies --cookie-file CHEMIN` accepte un export JSON fourni par l'utilisateur, uniquement pour les domaines Moodle/Teams autorisés. L'import n'affiche pas les valeurs. Il ne lit jamais directement les profils ou bases de cookies du navigateur personnel. Des cookies seuls peuvent être insuffisants pour Teams : Microsoft peut demander une nouvelle connexion/MFA sur le VPS.

Le navigateur dédié du VPS conserve Teams dans son profil. Les cookies Moodle, y compris ceux dont le chemin est `/2026/` et ceux de session, sont enregistrés séparément en mode 0600 puis restaurés avant chaque collecte. Ils proviennent uniquement de ce navigateur distant. Un contrôle HTTP protégé toutes les vingt minutes évite l'expiration par inactivité ; il ne garantit pas de contourner une expiration absolue ou une révocation côté université. Une reconnexion pourra alors être nécessaire.

Le bureau noVNC temporaire est **fermé après connexion**. Ses ports 5901 et 6081 n'écoutaient qu'en boucle locale, derrière une route Nginx réservée au client WireGuard `10.77.77.2`, avec identifiants dédiés et fermeture automatique en une heure maximum. La route et le mot de passe HTTP temporaires ont été retirés ; le profil et les cookies Moodle sont conservés. SSH et UFW n'ont pas été assouplis.

Pour une reconnexion demandée par l'utilisateur, rouvrir seulement cette route limitée avec `configure-login-proxy.py`, puis utiliser `https://relix.vps:8443/cours-watch/vnc.html?path=websockify`. Identifiant HTTP `cours-watch`, mot de passe root dans `/etc/cours-watch/login-http-password.txt`, mot de passe VNC dans `/var/lib/cours-watch/vnc-password.txt`. À chaque réouverture, le mot de passe HTTP change : rouvrir l'onglet pour ne pas réutiliser l'ancien en cache. Ne jamais copier ces valeurs dans les notes. Fermer ensuite avec `/usr/local/sbin/cours-watch-close-login`.

## Vérifications d'exploitation

1. Connecter Moodle et Teams sur le VPS ou importer un export autorisé ; fermer le navigateur de login pour libérer le profil.
2. Exécuter `cours_watch.py scan --dry-run` sous `courswatch`, et vérifier les huit sources après redémarrage du navigateur. Examiner les limites de couverture réelles et adapter les sélecteurs si nécessaire.
3. Discord est configuré et testé pour le rôle UPB ; vérifier que les quatre personnes possèdent ce rôle.
4. Une première lecture réussie crée sa référence sans annoncer les anciens contenus. `/etc/cours-watch/ready` peut être retiré pour suspendre les collectes sans effacer les références.
5. Vérifier le service sous systemd, ses journaux, le timer et le fichier de bilan. Arrêter l'interface de login temporaire et retirer sa route.

Commandes d'exploitation : `sudo systemctl status cours-watch.timer cours-watch-session.timer` ; `sudo journalctl -u cours-watch.service --no-pager` ; `sudo -u courswatch /opt/cours-watch/venv/bin/python /opt/cours-watch/cours_watch.py status`. Pour arrêter la veille et le maintien de session : `sudo systemctl disable --now cours-watch.timer cours-watch-session.timer`.

## Sources techniques

- [Playwright, authentification et protection des états](https://playwright.dev/python/docs/auth).
- [Playwright, Chrome stable](https://playwright.dev/python/docs/browsers#google-chrome--microsoft-edge).
- [Timers systemd](https://www.freedesktop.org/software/systemd/man/latest/systemd.timer.html).
- [Discord, applications installées par utilisateur et interactions privées](https://github.com/discord/discord-api-docs/blob/main/developers/tutorials/developing-a-user-installable-app.mdx).
