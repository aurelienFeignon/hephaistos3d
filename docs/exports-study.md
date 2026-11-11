# Étude des exports (P4.6, P4.7)

État : P4.6 mesurée le 2026-11-11 ; P4.7 à venir. Aucun export n'entre dans le produit sans mesure de ce
qu'il perd (ADR-0007).

## P4.6 — Conteneurs splat compressés : SPZ, SOG

Ces formats gardent **la même représentation** (des gaussiennes) et la compressent. La question n'est donc
pas « que perd-on en changeant de nature ? » mais « combien gagne-t-on en taille, et l'écart est-il visible ? ».

### Méthode

- **Entrées** : trois sorties réelles d'Idol — un fauteuil, une figurine, une paire de chaussures — chacune en
  262 144 et en 1 048 576 gaussiennes. Ce sont les PLY natifs du produit : position, couleur de base
  (`f_dc`), opacité, échelle, rotation. **Aucune harmonique sphérique d'ordre supérieur** : Idol n'en
  produit pas, si bien que la part la plus coûteuse de SOG (leur compression) est ici sans objet.
- **Outil** : `@playcanvas/splat-transform` 3.10.3 (MIT), en ligne de commande, **sur processeur**
  (`-g cpu`), réglages par défaut (SPZ version 4). Le format « PLY compressé » de PlayCanvas est mesuré à
  titre de comparaison.
- **Temps** : temps réel d'une seule conversion par cas, sur la machine de développement ; un ordre de
  grandeur, pas une moyenne.
- **Écart visuel** : chaque fichier compressé est redécodé en PLY par le même outil, puis rendu dans le
  **vrai visualiseur** du Studio (Spark, Chromium, rendu Vulkan) à la caméra prédite par Idol, et comparé
  au rendu du PLY d'origine. Mesures restreintes aux pixels de l'objet (le fond, identique partout,
  gonflerait le PSNR) : PSNR, part des pixels qui s'écartent de plus de 8 niveaux sur 255, écart maximal.
- **Plancher de bruit** : le PLY d'origine rendu deux fois donne deux images **identiques au pixel près**.
  Tout l'écart mesuré vient donc de la compression, pas du rendu.

### Taille et temps de conversion

| Objet | Gaussiennes | PLY | SPZ | SOG | PLY compressé |
|---|---|---|---|---|---|
| Fauteuil | 262 144 | 17,8 Mo | 3,4 Mo (÷5,3) · 0,7 s | 3,1 Mo (÷5,8) · 1,1 s | 4,3 Mo (÷4,2) · 0,4 s |
| Fauteuil | 1 048 576 | 71,3 Mo | 12,7 Mo (÷5,6) · 2,4 s | 11,8 Mo (÷6,1) · 2,2 s | 17,1 Mo (÷4,2) · 1,1 s |
| Figurine | 262 144 | 17,8 Mo | 3,4 Mo (÷5,2) · 0,7 s | 3,1 Mo (÷5,8) · 0,9 s | 4,3 Mo (÷4,2) · 0,4 s |
| Figurine | 1 048 576 | 71,3 Mo | 12,9 Mo (÷5,5) · 2,2 s | 11,6 Mo (÷6,2) · 1,8 s | 17,1 Mo (÷4,2) · 1,1 s |
| Chaussures | 262 144 | 17,8 Mo | 3,3 Mo (÷5,4) · 0,7 s | 3,1 Mo (÷5,8) · 1,2 s | 4,3 Mo (÷4,2) · 0,4 s |
| Chaussures | 1 048 576 | 71,3 Mo | 12,3 Mo (÷5,8) · 2,2 s | 11,5 Mo (÷6,2) · 1,9 s | 17,1 Mo (÷4,2) · 1,1 s |

### Écart visuel (rendu à la caméra d'Idol, pixels de l'objet)

Chaque case : PSNR · part des pixels à plus de 8/255 d'écart · écart maximal sur 255.

| Objet | Gaussiennes | SPZ | SOG | PLY compressé |
|---|---|---|---|---|
| Fauteuil | 262 144 | 46,43 dB · 0,07 % · max 16 | 46,43 dB · 0,02 % · max 24 | 50,48 dB · 0,01 % · max 21 |
| Fauteuil | 1 048 576 | 44,66 dB · 0,15 % · max 31 | 45,19 dB · 0,04 % · max 18 | 50,64 dB · 0,00 % · max 9 |
| Figurine | 262 144 | 43,96 dB · 0,16 % · max 19 | 44,05 dB · 0,30 % · max 20 | 47,71 dB · 0,00 % · max 10 |
| Figurine | 1 048 576 | 42,62 dB · 0,50 % · max 19 | 42,36 dB · 1,14 % · max 24 | 47,89 dB · 0,00 % · max 9 |
| Chaussures | 262 144 | 43,97 dB · 0,27 % · max 23 | 45,33 dB · 0,01 % · max 11 | 49,88 dB · 0,00 % · max 10 |
| Chaussures | 1 048 576 | 42,70 dB · 0,44 % · max 32 | 44,26 dB · 0,03 % · max 18 | 50,53 dB · 0,00 % · max 16 |

- Dans le pire cas (SOG, figurine, 1 M), la carte d'écart amplifiée huit fois ne montre qu'un **bruit fin
  réparti sur l'objet** : ni bandes, ni blocs, ni couleurs décalées. À l'œil, les rendus sont indiscernables.
- **SPZ**, seul des trois à conserver l'ordre des gaussiennes, se mesure aussi attribut par attribut :
  position à moins de 0,017 % de la diagonale de l'objet, opacité à 0,002 près, log-échelle à 0,031 près,
  et **couleur affichée à moins de 1/255**. Les écarts bruts plus grands sur `f_dc` (jusqu'à 4,7) portent
  tous sur des valeurs hors de la plage affichable, déjà saturées à l'écran : ils ne se voient pas.
- SOG et le PLY compressé **réordonnent** les gaussiennes (tri spatial) : sans effet sur le rendu, mais la
  comparaison ligne à ligne n'a pas de sens pour eux.

### Conclusion

| Format | Gain | Écart | Classement |
|---|---|---|---|
| **SPZ** | ÷5,2 à 5,8 | invisible (42,6–46,4 dB sur l'objet) | **livrable** — format ouvert, lu par Spark (autres lecteurs non vérifiés ici) |
| **SOG** | ÷5,8 à 6,2 (le meilleur) | invisible (42,4–46,4 dB) | **livrable** — écosystème PlayCanvas surtout |
| PLY compressé | ÷4,2 | le plus faible (47,7–50,6 dB) | possible, mais propre à PlayCanvas et moins compact |

- Les deux formats compressés sont des **exports fidèles au sens visuel** pour les sorties d'Idol, mesure à
  l'appui ; le PLY natif reste la référence exacte (ADR-0007).
- **Bénéfice secondaire** : servir du SPZ au visualiseur ramènerait le transfert d'un résultat d'un million
  de gaussiennes de 71 Mo à environ 12 Mo, le point que le plan signale comme risqué à travers le proxy.
  Ce n'est pas décidé : à mettre en regard de A4.1.
- **Coût d'intégration**, si on le décide : un artefact `EXPORT_*` produit de façon asynchrone
  (`api-contract.md` §5), un outil Node de plus dans une image, et le type de fichier transmis à Spark
  (le visualiseur force aujourd'hui `PLY`).

### Limites

- L'écart visuel est mesuré après **redécodage par `splat-transform`**, pas par le décodeur de Spark
  lui-même (le visualiseur n'accepte encore que du PLY). Un lecteur tiers peut décoder différemment.
- Une seule vue par objet (la caméra d'Idol) et des temps mesurés une seule fois.
- Trois objets, sans harmoniques sphériques : un modèle qui en produirait changerait les chiffres de SOG.

## P4.7 — GLB et maillage

À venir.
