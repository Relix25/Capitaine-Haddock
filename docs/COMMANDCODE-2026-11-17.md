# DeepSeek via CommandCode — 17 novembre 2026

Après l'arrêt explicite du bot, l'utilisateur a demandé de basculer les conversations sur DeepSeek avec son abonnement CommandCode, puis de multiplier le plafond quotidien par dix. Le code applicatif `2cb562fcd8a8e974fb3ea6fa74c14a78144d0770` et la configuration correspondante sont installés sur le VPS. Après l'autorisation des tests et leur réussite décrites plus bas, le service Discord a été remis en route et vérifié prêt.

## Configuration installée

| Paramètre | Valeur |
|---|---|
| Fournisseur | `commandcode` |
| Modèle | `deepseek/deepseek-v4.1-flash` |
| Endpoint fixe | `https://api.commandcode.ai/provider/v1/responses` |
| Clé privée | `CMD_API_KEY` dans `/etc/cours-watch/.env` |
| Plafond du bot | 10 000 000 tokens par jour, remise à zéro à minuit à Bucarest |

Le catalogue public CommandCode consulté le jour du changement annonce `/responses` pour ce modèle. La [documentation Provider API](https://commandcode.ai/docs/provider) décrit l'utilisation de la clé du compte avec les abonnements éligibles et le schéma Responses. Le compteur du bot ne change pas les plafonds propres à l'abonnement.

La clé a été lue dans le fichier d'authentification CommandCode local existant puis transmise par l'entrée standard de la connexion SSH à empreinte épinglée. Sa valeur n'a été ni affichée, ni placée dans un argument de commande, ni écrite dans le dépôt ou dans une archive. Le fichier privé du VPS conserve ses droits root:courswatch 0640 ; sa lecture sous le compte de service a été vérifiée. La clé n'est pas exportée aux processus de collecte ou de traduction.

Le fournisseur historique OpenAI reste disponible pour compatibilité lorsque `provider` manque ou vaut `openai`. La configuration active sélectionne explicitement CommandCode avec sa clé distincte ; une erreur ou une clé manquante ne déclenche aucun repli automatique sur OpenAI.

Le bot conserve sa personnalité, son contexte court en RAM, sa validation stricte des décisions JSON et sa limite de sortie. Les requêtes CommandCode imposent `store: false` et `x-cmd-zdr: 1`. Selon la [documentation ZDR](https://commandcode.ai/docs/resources/zdr), ce dernier en-tête exige une route sans conservation des prompts/réponses ni entraînement ; l'API peut refuser la requête si aucune route éligible n'est disponible. L'essai réel autorisé décrit plus bas a réussi avec cet en-tête ; cette réussite ne garantit pas la disponibilité future de la route.

## Validation et déploiement

- 248 tests exécutés sur Windows : 244 réussis et quatre tests de liens symboliques ignorés.
- Même source figée testée sous Linux, compte courswatch, Python 3.14.4 : 248 réussis, aucun test ignoré. Ces tests utilisent des transports simulés ; aucun appel payant n'en fait partie.
- Les tests vérifient l'endpoint et la clé distincts, l'en-tête ZDR, le schéma de sortie, l'absence de repli, la comptabilité conservée lors du changement de fournisseur/plafond et le rendu du nouveau statut.
- Installation et retour arrière répétés sur fixtures, y compris une panne injectée après remplacement du code, de la configuration et de la clé.
- Quatre fichiers de code remplacés : `courswatch/conversation/brain.py`, `courswatch/discord_app/bot.py`, `courswatch/discord_app/health.py` et `verify_bot_live.py`. Les 65 empreintes du runtime sont conformes au manifeste.
- Seuls le fournisseur, le modèle et le plafond de `discord.luna` ont changé dans la configuration ; une entrée `CMD_API_KEY` a été ajoutée au fichier privé existant. Les autres réglages, restrictions et secrets ont été préservés.
- Le plafond passe de 1 000 000 à 10 000 000 sans réinitialiser `luna-usage.db`. Les compteurs et réservations historiques sont identiques avant/après ; aucun blocage du jour n'était actif lors du contrôle.
- L'état des timers/watchers était inchangé à l'installation. Le service `cours-watch-bot.service` était alors inactif, avec MainPID 0, conformément à la demande d'arrêt ; sa remise en route ultérieure est décrite ci-dessous.

L'archive propre contient 260 fichiers, SHA256 `8d6d78351f58d8e355b854d391775eeef28f1ca6d2538e7ba7ab67172876a0be`. Le staging est `/opt/cours-watch-staging/commandcode-2cb562f`. La sauvegarde privée `/var/backups/cours-watch/commandcode-20261117-2cb562f` contient les anciens fichiers, configuration, secrets et une copie SQLite cohérente du compteur ; elle reste sur le VPS. Le rollback vérifie le code installé, restaure code/configuration/secrets et laisse le bot arrêté. Il ne restaure pas une ancienne consommation.

## Vérification initialement en attente

Le contrôle d'approbation automatique a refusé l'unique réponse synthétique DeepSeek prévue, car elle consommerait le quota de l'abonnement sans autorisation explicite pour cet essai précis. Cette action n'a pas été exécutée et aucune autre voie de génération n'a été tentée.

Les contrôles effectués immédiatement après ce refus étaient uniquement des lectures de configuration, de permissions, d'empreintes, d'état systemd et de compteurs. Ils confirmaient la préparation du basculement, mais ne prouvaient pas encore l'accès effectif du forfait à la génération API, une réponse valide du modèle ou un fonctionnement Discord après redémarrage.

## Autorisation et vérification effectives

Le même jour, en réponse à la demande d'autorisation du test puis du redémarrage, l'utilisateur a autorisé « n'importe quel test » nécessaire au bot et demandé de l'inscrire dans [AGENTS.md](../AGENTS.md), avec les consignes de mise à jour GitHub. L'ancienne interdiction des sondes de modèle a été remplacée par cette autorisation explicite. Les tests unitaires restent isolés ; les essais réels sont exécutés séparément et respectent les quotas, la confidentialité et l'absence de ping de rôle.

Un unique appel synthétique au modèle configuré a ensuite réussi sous le compte courswatch : décision JSON valide, route CommandCode avec ZDR exigé, environ **2,428 s** pour l'appel et 1 889 tokens comptabilisés. Aucun texte de conversation existante ni fait de cours n'a été envoyé pour cet essai. La consommation historique a été conservée, la réservation libérée et aucun message Discord n'a été envoyé par ce test de modèle.

Le service a été démarré après cette réussite. Le contrôle a confirmé le PID systemd courant, `ready: true`, le fournisseur CommandCode, le modèle DeepSeek V4.1 Flash, le plafond de 10 millions et les quatre commandes `a-faire`, `actualiser`, `cours`, `status`. Les 65 empreintes du runtime correspondaient toujours au manifeste. Les journaux du nouveau processus ne présentaient aucune erreur de callback, interaction inconnue, limitation API, traceback ou blocage de heartbeat dans la fenêtre examinée.

Le test `ops.validate_channel --send-test` a ensuite créé un seul message synthétique, vérifié trois cartes et les callbacks de pagination, puis remplacé le même message par un bilan court. Tous les reçus contenaient zéro mention de rôle ou d'utilisateur ; les écritures imposaient `allowed_mentions` vide. [Message de validation](https://discord.com/channels/1376984512046563519/1570209088929800282/1572071270991011963).

La vérification du salon utilise un adaptateur des callbacks et le transport Discord réel ; elle ne constitue pas un clic humain sur une commande slash. Les interactions privées restent couvertes par les tests isolés. Les preuves techniques de génération, démarrage et salon sont conservées uniquement dans la sauvegarde privée du VPS ; aucune réponse textuelle du modèle n'a été ajoutée au dépôt.
