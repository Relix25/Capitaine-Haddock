# Profils de validation hors ligne

Les implémentations sont rangées dans `tests/unit`, `tests/integration`,
`tests/migration` et `tests/operations`. Le runner sélectionne explicitement
leurs modules canoniques, une seule fois ; les 27 adaptateurs racine ont été
retirés. Il ne découvre aucun installateur, script `smoke_*` ou `probe_*`.
Un test précis se lance par `python -m unittest tests.unit.test_nom`.

```bash
python -B tools/run_tests.py core
python -B tools/run_tests.py conversation
python -B tools/run_tests.py documents
python -B tools/run_tests.py migration
python -B tools/run_tests.py regression
python -B tools/run_tests.py operations
python -B tools/run_tests.py all
```

`--list` affiche les modules sans les importer. `--site-packages CHEMIN` permet
d'utiliser un environnement de dépendances déjà installé, sans installation.

| Profil | Contrats vérifiés |
|---|---|
| `core` | Commandes, tâches, rappels, accès privés, présentation, santé, outbox |
| `conversation` | Luna/quota avec transport factice, ESN, voix, répliques, traduction |
| `documents` | Collecte PDF factice, identités des pièces jointes, versions SHA256, publication et notes gérées |
| `migration` | Configuration Syncthing synthétique et migration historique sur fixtures |
| `regression` | Transitions de fraîcheur et de pipeline, migrations de schéma, imports canoniques et points d’entrée |
| `operations` | Construction et intégrité des lots, inventaire factice, outils importables sans effets |
| `all` | Union des six profils explicites, sans doublon |

Le runner remplace les chemins de configuration par des chemins synthétiques,
retire les clés API et jetons connus de l'environnement, et refuse les connexions
externes ainsi que l'accès aux répertoires de production. Les sockets loopback
nécessaires à asyncio sous Windows restent autorisées. Les sous-processus des
tests CLI héritent du même garde-fou via `sitecustomize`.

Les fixtures sont créées dans un dossier temporaire propre à chaque exécution,
sous `.test-tmp/` par défaut, puis nettoyées. `--temp-root CHEMIN` permet de choisir
un autre emplacement de test. Ne jamais fournir le coffre réel ou un répertoire
de service. Les tests ne doivent pas dépendre d'une configuration installée.

Le garde-fou est une protection contre les effets de bord accidentels, pas une
sandbox pour du code hostile. Les dépendances doivent être installées séparément
avant l'exécution : `discord.py`, `pypdf` et les données `tzdata` sont nécessaires
à la suite complète. Aucun modèle de traduction ni accès OpenAI n'est requis.

Les cas de permissions POSIX et de liens symboliques doivent aussi être exécutés
sous Linux. Un test ignoré sous Windows n'est pas une validation de ces contrats.
