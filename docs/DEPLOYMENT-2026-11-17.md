# Déploiement vérifié du 17 novembre 2026

Cette note décrit les vérifications techniques sans reproduire de données de cours, secrets, sessions ou rapports privés.

## Version installée

Le commit applicatif `3d9e6d45bfafa816f198b7cc0b8cafe19cff1589` a été installé dans `/opt/cours-watch` après demande explicite de l'utilisateur. Le lot propre contient 256 fichiers de code, tests, outils et documentation ; son SHA256 est `82bc8bad7ca87d9811c915106db39690d8b4d2c51c29bfcd7e56b8cc12938517`.

L'installation est limitée à 64 fichiers runtime et outils, dont le package complet, ses adaptateurs et l'outil de validation du salon. Les tests et les outils historiques complets restent dans le dépôt et le staging ; le VPS actif ne devient pas une copie globale du dépôt. Les empreintes installées ont toutes été comparées au manifeste du lot. `/opt/cours-watch/RELEASE.json` conserve le commit, les empreintes et le chemin de sauvegarde.

Les 75 fichiers Python initialement installés correspondaient au commit initial `d7047fb` ; aucune divergence de code inattendue n'a été écrasée.

## Vérifications exécutées

| Contrôle | Résultat |
|---|---|
| Suite complète Windows, Python 3.12.14 | 225 exécutés : 221 réussis, 4 liens symboliques ignorés |
| Suite complète Linux sous courswatch, Python 3.14.4 | 225 réussis, aucun échec ou test ignoré |
| Correspondance du code figé avec l'arbre testé sur Linux | Tous les fichiers Python identiques par SHA256 |
| Imports dans collecte, bot et traduction | Réussis avec les trois environnements existants |
| Chargement des deux modèles locaux en_fr et ro_en | Réussi ; aucun téléchargement ni appel payant |
| Répétition du remplacement et du rollback | Réussie sur fixtures, y compris panne injectée après remplacement |
| Migrations additives sous courswatch | Réussies ; compteurs, file, reçus et quota conservés |
| Bot actif et prêt après redémarrage | Vérifié avec le PID systemd courant |
| Commandes enregistrées | a-faire, actualiser, cours, status |
| Rendu du cache en lecture seule | Ordre des prochaines échéances et absence de déduplication par URL vérifiés |

Le runner de tests identifie maintenant le coffre Windows par son emplacement réel au lieu de considérer tout parent d'un staging Linux comme un coffre. Le premier lancement Linux avait révélé cette erreur de garde-fou, corrigée avant installation ; la suite complète a ensuite été relancée avec succès.

## Vérification du salon sans mentions

`ops/validate_channel.py --send-test` a vérifié l'identité du bot, le serveur, le salon et les quatre commandes. Il a créé un seul message de données synthétiques, affiché trois cartes, appliqué les callbacks suivant/précédent sur deux pages, puis remplacé ce même message par un bilan court.

Toutes les écritures imposaient `allowed_mentions` avec `parse`, `users` et `roles` vides et `replied_user: false`. Tous les reçus avaient zéro mention de rôle ou d'utilisateur. Les boutons de navigation publics étaient désactivés pendant l'essai puis retirés. Aucun contenu pédagogique privé n'a été publié.

[Message de validation](https://discord.com/channels/1376984512046563519/1570209088929800282/1572044593233400001).

Cet essai vérifie le transport réel, les cartes et les éditions produites par les callbacks. Il ne constitue pas un clic humain sur une interaction slash ou une confirmation d'affichage éphémère. Ces contrats sont couverts par les tests isolés ; aucune confirmation humaine n'est inventée.

## Sauvegarde et retour à l'exploitation

La sauvegarde privée `/var/backups/cours-watch/refactor-20261117-3d9e6d4` conserve les anciens fichiers remplacés, des sauvegardes SQLite cohérentes avec WAL, l'état initial des unités et le script contrôlé de déploiement/rollback. Ces fichiers restent sur le VPS et ne sont pas importés dans Git.

Les configurations, secrets, marqueurs, unités systemd, environnements, modèles et assets sont restés en place. Les fichiers de configuration protégés ont été comparés avant/après sans être affichés. Les timers de collecte, maintien de session et ESN ainsi que le watcher Obsidian ont été remis dans leur état actif initial après les tests du salon.

Le script de rollback vérifie la version et l'absence de dérive du code avant un retour arrière explicite. Il restaure le code, conserve les nouvelles données et remet les unités dans leur état initial. Il ne restaure pas aveuglément une ancienne base, ne vide pas l'outbox et ne supprime pas les PDF. Aucun rollback de production n'a été nécessaire.

## Vérification restante

Une collecte manuelle réelle Moodle/Teams avec le nouveau code n'a pas été exécutée. Le contrôle d'approbation automatique a refusé cette action, car elle consulte des données privées et écrit dans les bases, rapports, notifications et éventuellement Obsidian. Une autorisation explicite complémentaire est requise pour cette vérification ; aucune tentative indirecte n'a été faite.

Le fonctionnement programmé préexistant a été rétabli, sans lancement manuel de collecte. Les références PDF sans vérification de la nouvelle génération peuvent rester affichées « à confirmer » jusqu'à une collecte réussie. L'état de session Moodle/Teams et le transport PDF sur des données réelles ne sont donc pas déclarés validés par ce déploiement.

La création du dépôt Git historique, l'archive de revue non commitée et les blocages de la première phase restent décrits dans `REFACTOR-2026-11-17.md`. Cette note constitue l'état ultérieur après autorisation de déploiement.
