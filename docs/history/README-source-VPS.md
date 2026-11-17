# Veille des cours UPB sur le VPS

Ce service consulte Moodle et Teams avec un navigateur dédié, compare les informations collectées aux dernières observations et envoie des alertes Discord ainsi qu'un rappel quotidien des projets. Il fonctionne sur le VPS lorsque le PC est éteint. Le cours Administrativ est exclu.

## État au 12 novembre 2026

- Environnement Python, Playwright et Chrome stable installés ; le sandbox Chrome est conservé et a été testé sous les restrictions du service.
- Vingt-huit tests couvrent les références, changements, reprises, cookies, liens de forum, fichiers, dates, rappels, embeds, contrôle d'accès aux interactions et confidentialité des réponses.
- `cours-watch.timer` est programmé chaque jour à **08:00 Europe/Bucharest** ; systemd gère le passage été/hiver et rattrape une échéance manquée.
- Les huit sources Moodle/Teams ont été lues avec les sessions du VPS. Le fichier `/etc/cours-watch/ready` active la collecte quotidienne. Le webhook Capitaine Haddock et le rôle UPB sont configurés et testés.
- Aucun cookie de la session locale n'a été exporté ni transféré. Le navigateur intégré n'expose pas cette fonction dans ses capacités disponibles.
- Les alertes de perte de session Moodle de la mise en route ont été suivies de leur rétablissement après correction de la persistance. La première référence des contenus est silencieuse.
- `cours-watch-session.timer` maintient Moodle actif avec une lecture HTTP légère toutes les vingt minutes, sans navigateur et sans notification de contenu.
- Les navigateurs disposent de 3 Gio maximum. Un verrou partagé évite les accès simultanés au profil ; le verrou de connexion est nettoyé même après un arrêt anormal.
- Le premier point réel sur les dix échéances a été envoyé en quatre messages, avec un seul ping UPB. Un second déclenchement n'a envoyé aucun doublon ; le rappel de 08:00 reste prévu.
- Les quatre messages initiaux ont été convertis en embeds compacts, sans nouvelle mention. L'application `1510767148006641866` est active : `/cours`, `/actualiser` et boutons privés, vérifiés avec Discord et par l'utilisateur. Les quatre cours disposent d'une nouvelle carte interactive.

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

Le client actif, `course_bot.py`, utilise `discord.py 2.7.1` dans `/opt/cours-watch/bot-venv`, avec seulement l'intent Guilds. Il n'utilise ni Message Content, ni Members, ni Presence. Il ajoute uniquement `/cours` et `/actualiser` dans le serveur configuré, en préservant les autres commandes de l'application. `cours-watch-bot.service` est actif et activé au démarrage du VPS.

- `/cours` affiche le dernier point en privé et accepte un filtre de cours.
- `/actualiser` relit Moodle et Teams puis affiche le point en privé. Les nouvelles informations suivent le canal de notification habituel ; les collectes concurrentes sont évitées.
- « Plus d’infos » répond de façon éphémère, visible uniquement par la personne qui clique. L'accès exige le serveur, le salon et le rôle UPB configurés. Une carte privée reste aussi liée à son demandeur.

Le `.env` se modifie avec `sudo nano /etc/cours-watch/.env`, ligne `DISCORD_BOT_TOKEN=...`. Il n'est jamais placé dans Git ou Obsidian. La validation est `sudo /opt/cours-watch/venv/bin/python /opt/cours-watch/configure_bot.py 1510767148006641866 --from-env`. Le jeton n'est pas affiché. `activate_bot.py` démarre l'écoute, vérifie les commandes et les droits, puis sélectionne `bot_channel` uniquement si tout est prêt. Le service reste derrière le marqueur `/etc/cours-watch/bot-ready`.

Les reçus publics sont dans `discord_receipts` ; les cartes privées sont liées à leur auteur dans `ui_messages`. L'état d'écoute est `/var/lib/cours-watch/reports/bot-status.json`. Les boutons publics sont enregistrés de façon persistante pour survivre aux redémarrages du client.

Validation réelle du 12 novembre : application authentifiée sous le nom Capitaine Haddock, commandes retrouvées via l'API, embeds et composants acceptés, accès au salon et mention UPB autorisés. Un clic réel a déclenché une réponse privée et l'utilisateur a confirmé le fonctionnement des commandes. [Carte SOS interactive](https://discord.com/channels/1376984512046563519/1570209088929800282/1570230953404665994). Le transport courant est `bot_channel`, avec `presentation: embed` et `interactive: true` ; le webhook existant est conservé comme configuration de secours.

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
