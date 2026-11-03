# ADR-0007 — Exports : PLY natif seul au MVP

- **Statut** : Accepté (2026-11-02)
- **Date** : 2026-11-02

## Contexte
L'utilisateur souhaite PLY, d'autres formats splat, GLB et OBJ ; les conversions GLB/OBJ sont
déclarées expérimentales.

## Décision
- MVP : PLY natif (plus l'aperçu prétraité et la caméra prédite).
- P4 : étude chiffrée de trois catégories distinctes — conversion de conteneur splat (SPZ, SOG),
  splats dans un glTF, reconstruction de maillage.
- Un nuage de points des centres n'est **jamais** présenté comme un export 3D de l'objet.

## Conséquences
- Aucun export approximatif n'entre dans le produit sans mesure de ce qu'il perd.

## Alternatives écartées
- Livrer GLB/OBJ par nuage de points dès le MVP : trompeur (perte des échelles, rotations, opacités).
