# Préparation des lots et exploitation après refactoring

Cette procédure prépare les lots de code, leur validation et leur remplacement contrôlé. Les outils de préparation n'installent aucun fichier et ne redémarrent aucun service par eux-mêmes. Les anciens installateurs partiels et activations figées ont été retirés ; leurs sources restent dans Git.

## Source et unités de référence

Le dépôt Git Haddock est la source des changements. La copie `veille-cours` ne doit pas servir de source d'un nouveau lot ; elle reste conservée tant que ses appelants externes ne sont pas inventoriés. Les chemins CLI historiques sont maintenus.

`systemd/` est l'unique copie des unités et de leurs drop-ins. Les copies à la racine ont été retirées avec leurs installateurs obsolètes. Les deux anciennes variantes de service avaient intégré le droit d'écriture du drop-in dans le fichier principal ; conserver ensemble la base et son drop-in sous `systemd/`. Avant toute évolution, comparer les propriétés effectives sur l'hôte : identités, chemins autorisés, conditions d'activation, timers et permissions. Les restrictions ne doivent pas être élargies.

Les outils `install_translation_models.py`, `install_reminders.py` et `verify_bot_live.py` sont désormais importables sans déclencher leur opération. Leur lancement direct conserve leur fonction historique ; le dernier appelle Discord et écrit une preuve. Ils ne font pas partie des tests locaux automatiques.

## Construire et vérifier un lot local

Depuis le dépôt :

```text
python -B ops/build_release.py --output /chemin/staging/haddock.tar.gz
python -B ops/build_release.py --verify /chemin/staging/haddock.tar.gz
```

Le répertoire parent de sortie doit déjà exister. L'outil refuse d'écraser une archive. Il utilise une liste de chemins/extensions autorisés : code, tests, outils, unités, documentation, dépendances et assets. Les configurations d'hôte, bases, rapports, environnements, modèles et sessions sont exclus. Les liens symboliques de sources sont refusés.

Le manifeste embarqué conserve le commit complet, l'indicateur de modifications locales, les dépendances déclarées, la taille et le SHA256 de chaque fichier. Les octets archivés sont les mêmes que les octets hachés ; les métadonnées tar/gzip sont normalisées pour obtenir la même archive à partir du même instantané. L'outil de vérification contrôle les chemins, l'inventaire, les doublons, les types de membres et toutes les empreintes avant toute utilisation.

Un checkout modifié est refusé par défaut. `--allow-dirty` permet de préparer un lot de revue explicitement marqué `dirty: true` ; ce lot ne doit pas être présenté comme le contenu exact du commit. Les modifications doivent être relues et figées avant une publication ou un déploiement réel. Ne pas modifier les sources pendant la construction.

Le lot inclut le prompt utilisateur `courswatch/conversation/prompt-haddock.txt` en plus du code Python ; sa présence et ses octets sont vérifiés par les tests. Le script ne recrée pas les trois environnements et ne garantit pas à lui seul une installation reproductible de Chrome ou des paquets système. Leurs versions effectives et les modèles doivent être relevés séparément.

## Modèles de traduction

```text
python -B ops/verify_models.py --manifest /chemin/revu/modeles.json --archives /chemin/archives
```

Le manifeste doit contenir exactement `en_fr` et `ro_en`, avec pour chacun `version`, `source` et `sha256`. Les SHA attendus doivent provenir d'une revue préalable : aucune empreinte n'est inventée ou acquise automatiquement au téléchargement. Le vérificateur ne télécharge et n'extrait rien ; il refuse toute archive locale qui ne correspond pas au manifeste. Le manifeste historique produit après installation n'est pas automatiquement une preuve d'approbation.

## Inventaire VPS en lecture seule

`ops/inventory_runtime.py` calcule les SHA des sources sélectionnées, relève les versions des environnements et demande uniquement des propriétés systemd non secrètes. Il ne lit pas `.env`, la configuration Discord, les cookies, les bases ou les rapports, et n'affiche pas `Environment` ou les arguments de commandes pouvant contenir des données privées. Relever l'état chargé et les permissions effectives avant chaque déploiement ; les preuves datées ne remplacent pas ce contrôle.

## Installation et retour arrière

1. Relever les SHA actifs et unités effectives ; vérifier l'absence de modifications concurrentes. Construire un lot revu depuis Git et le vérifier.
2. Tester le lot complet dans un staging Linux avec les versions des trois environnements, sans accès aux secrets ni aux plateformes. Valider les imports historiques et nouveaux, les tests, les unités et les drop-ins.
3. Préparer une sauvegarde unique du lot actif et de ses métadonnées, puis une liste précise des fichiers à remplacer. Utiliser une sauvegarde SQLite cohérente avec WAL pour toute évolution de schéma ; ne pas copier aveuglément les seuls fichiers `.db`.
4. Lors du déploiement demandé, coordonner les processus concernés et remplacer seulement les fichiers prévus par le lot revu. Ne jamais synchroniser globalement vers `/etc/cours-watch` ou `/var/lib/cours-watch`. Le nettoyage de sources Git ne demande pas de supprimer les copies historiques présentes sur le VPS.
5. En cas d'échec, restaurer ensemble l'ancien code et les unités/drop-ins sauvegardés. Vérifier le PID courant, l'état prêt, les commandes et les fonctions concernées. Publier ensuite le résultat et les limites sur GitHub selon [AGENTS.md](../AGENTS.md).

Un retour arrière de code ne doit pas effacer des événements reçus depuis la sauvegarde, vider l'outbox, réinitialiser le quota ou supprimer une version PDF. Les migrations additives doivent rester lisibles par l'ancien code ; un retour arrière de données nécessite une procédure dédiée et une réconciliation des nouveaux événements. Répéter le remplacement et le retour arrière adaptés au changement avant de les appliquer à la production ; les tests d'archives seuls ne les remplacent pas. Les opérations déjà réalisées figurent dans [le compte rendu de déploiement](history/DEPLOYMENT-2026-11-17.md) et [le compte rendu CommandCode](COMMANDCODE-2026-11-17.md).
