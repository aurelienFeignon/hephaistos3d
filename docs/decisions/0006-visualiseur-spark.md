# ADR-0006 — Visualiseur Gaussian Splat : Spark

- **Statut** : Accepté (2026-11-02)
- **Date** : 2026-11-02

## Contexte
Idol produit des PLY 3DGS (ordre 0 des harmoniques sphériques). Un chargeur PLY classique
afficherait des points, pas des gaussiennes.

## Décision
`@sparkjsdev/spark` (MIT, 2.4.0 publiée le 2026-10-20, Three.js ≥ 0.180) : lit PLY, PLY compressé,
SPZ, SPLAT, KSPLAT, SOG ; s'intègre à une scène Three.js ; caméra gérée par `OrbitControls`.

## Conséquences
- Three.js reste la base demandée ; Spark s'y ajoute comme maillage spécialisé.
- Les formats compressés (SPZ, SOG) seront lisibles sans changer de visualiseur.
- Le chargement progressif d'un PLY n'est pas vérifié ; repli : progression d'octets réelle.

## Alternatives écartées
- `@mkkellogg/gaussian-splats-3d` : dernière version en janvier 2025, maintenance incertaine.
- Visualiseur PlayCanvas / SuperSplat : hors de Three.js, demandé par la pile.
