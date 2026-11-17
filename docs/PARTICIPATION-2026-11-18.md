# Participation de Haddock et sources du salon

Pour une mention explicite ou une réponse à Haddock, le bot traite directement la demande. En mode sélectif, les messages ambigus autorisés, y compris les relances courtes, passent par un vote sur leur sens. Une relance du même interlocuteur pendant les deux minutes qui suivent une réponse peut inclure la question précédente et sa réponse, avec validation de leurs versions et droits. Les limites de cadence et le quota quotidien restent actifs.

Le juge est `typesafe/jev-1.14.0` sur `/provider/v1/systemone`. Il reçoit du texte borné, des rôles de conversation anonymisés et une question binaire. Le seuil de réponse est 0,5. Si le profil exige ZDR, la sélection utilise le modèle de texte actif sous la même exigence. Jev ne propose pas de route ZDR au 18 novembre 2026. Une erreur du juge fait taire le bot sur les demandes ambiguës et laisse une trace technique dans `last_participation`.

HY4 utilise Chat Completions, `reasoning_effort=off` et le JSON strict. DeepSeek utilise Responses avec ZDR. Une réponse sur les échéances réutilise les sources autorisées du salon et rend les dates avec leurs preuves. L’autorisation des sources se configure sous `discord.luna.knowledge.allowed_sources` pour le triplet serveur, salon et fil. Le propriétaire a autorisé dans #upb tout ce qui concerne les cours, le 18 novembre 2026.

## Validation opérateur

Depuis le lot installé et avec les dépendances du service, `python -B -m ops.probes.verify_conversation --live-models` exécute la salutation, la question AAIT, sa relance SOS et un échange entre humains. Les modèles et le compteur du bot sont réels. Les participants, les documents et le transport Discord sont synthétiques. La sonde `python -B -m ops.validate_channel --send-test` vérifie en complément le transport Discord et les reçus sans mentions.

[Référence CommandCode pour Jev](https://commandcode.ai/models/jev-1-14-0) et [contrats d’API par modèle](https://commandcode.ai/docs/provider).
