# Médias de test synthétiques

Ces trois petites images ont été créées localement avec Pillow 13.0.0. Elles ne
contiennent ni personnes ni données de cours réelles.

- `synthetic-date.png` : texte fictif « TEST 2099 ».
- `late-overlay.gif` : première image blanche, puis couleurs et texte fictif
  apparaissant brièvement à 700 ms ; durées 100, 600, 40 et 400 ms.
- `animated-emoji.webp` : même séquence, encodée en WebP animé.

Les tests vérifient le décodage réel local et des descriptions/OCR simulés.
Aucun modèle fournisseur n'a analysé ces fichiers dans cette validation.
