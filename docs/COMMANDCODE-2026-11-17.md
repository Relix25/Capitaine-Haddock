# DeepSeek via CommandCode — 17 novembre 2026

Après l'arrêt explicite du bot, l'utilisateur a demandé de basculer les conversations sur DeepSeek avec son abonnement CommandCode, puis de multiplier le plafond quotidien par dix. Le code applicatif `2cb562fcd8a8e974fb3ea6fa74c14a78144d0770` et la configuration correspondante sont installés sur le VPS. Le service Discord reste arrêté.

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

Le bot conserve sa personnalité, son contexte court en RAM, sa validation stricte des décisions JSON et sa limite de sortie. Les requêtes CommandCode imposent `store: false` et `x-cmd-zdr: 1`. Selon la [documentation ZDR](https://commandcode.ai/docs/resources/zdr), ce dernier en-tête exige une route sans conservation des prompts/réponses ni entraînement ; l'API peut refuser la requête si aucune route éligible n'est disponible. La disponibilité réelle de cette route n'a pas été testée par génération.

## Validation et déploiement

- 248 tests exécutés sur Windows : 244 réussis et quatre tests de liens symboliques ignorés.
- Même source figée testée sous Linux, compte courswatch, Python 3.14.4 : 248 réussis, aucun test ignoré. Ces tests utilisent des transports simulés ; aucun appel payant n'en fait partie.
- Les tests vérifient l'endpoint et la clé distincts, l'en-tête ZDR, le schéma de sortie, l'absence de repli, la comptabilité conservée lors du changement de fournisseur/plafond et le rendu du nouveau statut.
- Installation et retour arrière répétés sur fixtures, y compris une panne injectée après remplacement du code, de la configuration et de la clé.
- Quatre fichiers de code remplacés : `courswatch/conversation/brain.py`, `courswatch/discord_app/bot.py`, `courswatch/discord_app/health.py` et `verify_bot_live.py`. Les 65 empreintes du runtime sont conformes au manifeste.
- Seuls le fournisseur, le modèle et le plafond de `discord.luna` ont changé dans la configuration ; une entrée `CMD_API_KEY` a été ajoutée au fichier privé existant. Les autres réglages, restrictions et secrets ont été préservés.
- Le plafond passe de 1 000 000 à 10 000 000 sans réinitialiser `luna-usage.db`. Les compteurs et réservations historiques sont identiques avant/après ; aucun blocage du jour n'était actif lors du contrôle.
- L'état des timers/watchers est inchangé. Le service `cours-watch-bot.service` est inactif, avec MainPID 0, conformément à la demande d'arrêt.

L'archive propre contient 260 fichiers, SHA256 `8d6d78351f58d8e355b854d391775eeef28f1ca6d2538e7ba7ab67172876a0be`. Le staging est `/opt/cours-watch-staging/commandcode-2cb562f`. La sauvegarde privée `/var/backups/cours-watch/commandcode-20261117-2cb562f` contient les anciens fichiers, configuration, secrets et une copie SQLite cohérente du compteur ; elle reste sur le VPS. Le rollback vérifie le code installé, restaure code/configuration/secrets et laisse le bot arrêté. Il ne restaure pas une ancienne consommation.

## Vérification réelle en attente

Le contrôle d'approbation automatique a refusé l'unique réponse synthétique DeepSeek prévue, car elle consommerait le quota de l'abonnement sans autorisation explicite pour cet essai précis. Cette action n'a pas été exécutée et aucune autre voie de génération n'a été tentée.

Les contrôles effectués après ce refus sont uniquement des lectures de configuration, de permissions, d'empreintes, d'état systemd et de compteurs. Ils confirment la préparation du basculement, mais ne prouvent ni l'accès effectif du forfait à la génération API, ni une réponse valide du modèle, ni un fonctionnement Discord après redémarrage. Le test réel et la remise en route attendent une instruction explicite de l'utilisateur.
