# Étude des exports (P4.6, P4.7)

État au 2026-11-11 : P4.6 et P4.7 mesurées ; classement A4.5 en fin de document. Aucun export n'entre dans le produit sans mesure de ce
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

État : mesurée le 2026-11-11 (GLB de splats, nuage de points, maillage).

### Splats dans un GLB (`KHR_gaussian_splatting`)

`splat-transform` 3.10.3 écrit un GLB qui suit l'extension Khronos `KHR_gaussian_splatting` : une primitive
en points (`mode 0`) qui porte `POSITION` et `COLOR_0` (couleur sur 8 bits), plus les attributs de
l'extension (rotation, échelle, opacité, couleur de base). L'extension est déclarée dans `extensionsUsed`
mais **pas** dans `extensionsRequired` : un lecteur qui l'ignore affiche le nuage de points coloré.

**Contenu : exact.** Relu attribut par attribut sur les six fichiers, contre le PLY d'origine :

| Attribut | Écart |
|---|---|
| Position | identique au bit près, après une rotation de 180° autour de z (`(−x, −y, z)`) qui redresse les splats d'Idol (y vers le bas) vers la convention glTF (y vers le haut) |
| Rotation | même rotation de 180° appliquée aux quaternions, à 1,8·10⁻⁷ près (renormalisation) |
| Couleur de base, opacité, échelle | identiques (l'opacité passe par la sigmoïde, l'échelle par l'exponentielle, à 6·10⁻⁸ près) |
| `COLOR_0` (repli) | couleur affichée quantifiée sur 8 bits, à ½ niveau près |

**Taille** : 15,7 Mo pour 262 144 gaussiennes et 62,9 Mo pour 1 048 576, contre 17,8 et 71,3 Mo en PLY
(les normales, inutilisées, disparaissent). Aucune compression : le GLB n'est pas un format compact.

**Lecteurs : immatures.** Rendus comparés au rendu Spark du PLY d'origine, même caméra et même cadrage
(Three.js 0.187, Chromium, pixels de l'objet) :

| Lecteur | Ce qui s'affiche | PSNR | Recouvrement de silhouette |
|---|---|---|---|
| `GLTFLoader` par défaut (tout lecteur glTF sans l'extension) | **nuage de points** clairsemé, piqueté, objet troué | 10,8 à 13,9 dB | 0,81 à 0,97 |
| `GLTFLoader` + `GLTFGaussianSplatLoaderExtension` (exige `WebGPURenderer`) | de vrais splats, délavés, halo clair sur les bords | 13,2 à 14,9 dB | 0,93 à 0,99 |
| idem, sortie du moteur en couleurs linéaires | couleurs plus proches, halo toujours là | 22,1 à 22,7 dB | — |

- Le délavement vient d'une **double conversion de couleur** : le GLB déclare des couleurs déjà prêtes à
  l'affichage (`srgb_rec709_display`) et le moteur les convertit encore. La couper rend 9 dB, mais le rendu
  reste nettement différent de Spark (76 à 89 % des pixels de l'objet à plus de 8/255) : l'écart restant
  tient au moteur, pas aux données, qui sont exactes.
- Spark 2.4, le visualiseur du produit, ne lit pas le GLB.
- Autres lecteurs (Blender, Babylon.js, PlayCanvas, visionneuses système) : **non testés ici**.

**Classement : expérimental.** Le fichier est fidèle, mais ce qu'on en voit dépend entièrement du lecteur,
et le seul lecteur testé qui rend des splats ne concorde pas avec le nôtre. À proposer, le cas échéant,
comme « format d'échange » avec cet avertissement, pas comme un rendu garanti.

### GLB / OBJ en nuage de points (centres des gaussiennes)

C'est exactement le repli ci-dessus : ce que montre un lecteur glTF sans l'extension. Mesuré à 10,8 à
13,9 dB, objet troué : échelles, rotations et opacités sont perdues.

**Classement : écarté.** ADR-0007 l'interdisait comme export 3D de l'objet ; la mesure le confirme.

### Reconstruction de maillage

**Méthode.** Les gaussiennes servent de points orientés : centre, et normale le long de leur axe le plus
court, en ne gardant que celles d'opacité supérieure à 0,2 (98 % d'entre elles). Les normales sont orientées
vers l'extérieur, puis rendues cohérentes localement. Une **reconstruction de Poisson** (Open3D 0.20.0,
profondeur 9) en tire une surface, débarrassée de ses 2 % de sommets les moins soutenus, puis chaque sommet
prend la couleur affichée de la gaussienne la plus proche. Export en GLB et en OBJ. Les variantes essayées
(orientation par le seul centre, profondeur 10) n'ont rien amélioré.

**Coût**, calcul plafonné à 12 Go, sur processeur :

| Objet | Gaussiennes | Triangles | GLB | OBJ | Temps | Mémoire max. |
|---|---|---|---|---|---|---|
| Fauteuil | 262 144 | 1,12 M | 44,8 Mo | 101,0 Mo | 23,5 s | 0,96 Go |
| Fauteuil | 1 048 576 | 2,43 M | 97,5 Mo | 224,1 Mo | 57,0 s | 2,14 Go |
| Figurine | 262 144 | 0,73 M | 29,1 Mo | 65,3 Mo | 13,9 s | 0,74 Go |
| Figurine | 1 048 576 | 1,35 M | 54,2 Mo | 123,0 Mo | 31,0 s | 1,50 Go |
| Chaussures | 262 144 | 1,12 M | 44,7 Mo | 101,0 Mo | 20,5 s | 0,95 Go |
| Chaussures | 1 048 576 | 1,72 M | 68,8 Mo | 155,9 Mo | 50,1 s | 2,43 Go |

Les fichiers ne sont **pas plus compacts que le PLY** d'origine : en GLB, de 0,76 à 2,5 fois sa taille
(plus légers seulement pour deux objets en 1 M de gaussiennes), en OBJ de 1,7 à 5,7 fois. Les temps
excluent le premier lancement (environ 60 s, chargement des bibliothèques compris).

**Géométrie.** La surface suit les gaussiennes : 95 % de ses points sont à moins de 0,31 à 1,41 % de la
diagonale de l'objet de la gaussienne la plus proche, et 95 % des gaussiennes à moins de 0,31 à 0,55 % de la
surface.

**Rendu** à la caméra d'Idol, comparé au rendu Spark des splats (Three.js, couleurs par sommet sans
éclairage, pixels de l'objet) :

| Objet | Gaussiennes | Simple face (lecteur ordinaire) | Double face (meilleur cas) |
|---|---|---|---|
| Fauteuil | 262 144 | 16,9 dB · 44 % > 8 · silhouette 0,91 | 21,9 dB · 22 % · 0,96 |
| Fauteuil | 1 048 576 | 14,5 dB · 61 % · 0,91 | 21,2 dB · 27 % · 0,97 |
| Figurine | 262 144 | 10,5 dB · 77 % · 0,82 | 15,6 dB · 41 % · 0,90 |
| Figurine | 1 048 576 | 10,6 dB · 83 % · 0,83 | 15,3 dB · 43 % · 0,90 |
| Chaussures | 262 144 | 11,0 dB · 67 % · 0,81 | 19,2 dB · 43 % · 0,94 |
| Chaussures | 1 048 576 | 9,9 dB · 74 % · 0,75 | 18,6 dB · 47 % · 0,94 |

(Pour mémoire, SPZ et SOG : 42 à 46 dB.) Ce qui se voit :

- **Triangles à l'envers.** Une partie de la surface est orientée vers l'intérieur : un lecteur qui élimine
  les faces arrière, comportement par défaut, montre des **trous** (visage de la figurine, dossier du
  fauteuil, chaussure creuse). Le rendu en double face prouve que la surface existe ; un export devrait au
  minimum déclarer `doubleSided` ou réorienter les triangles, ce qui n'est **pas vérifié** dans des lecteurs
  tiers.
- **Structures fines déchirées.** Le bord du casque part en lambeaux : Poisson suppose une surface fermée et
  épaisse.
- **Couleurs plus plates, détails plus mous** (lacets, capitons) : une couleur par sommet ne restitue pas le
  mélange des gaussiennes.

**Classement : écarté en l'état.** Ce n'est pas une reconstruction fidèle (A4.6) : défauts visibles dans un
lecteur ordinaire, fichiers pas plus compacts que le PLY, une minute de calcul et jusqu'à 2,4 Go de mémoire par
objet. Pour passer en « expérimental », il faudrait au moins une orientation de surface fiable et une
méthode qui respecte les structures fines (fusion de cartes de profondeur rendues depuis plusieurs vues, par
exemple) — chantier hors MVP.

### Classement (A4.5)

| Export | Nature | Classement | Raison |
|---|---|---|---|
| PLY natif | splats, exact | **livrable** (déjà livré) | référence |
| SPZ | splats compressés | **livrable** | ÷5,2 à 5,8, écart invisible (P4.6) |
| SOG | splats compressés | **livrable** | ÷5,8 à 6,2, écart invisible (P4.6) |
| GLB `KHR_gaussian_splatting` | splats, exact | **expérimental** | fichier fidèle, mais rendu dépendant de lecteurs immatures |
| GLB / OBJ nuage de points | centres seuls | **écarté** | objet troué, échelles, rotations et opacités perdues |
| GLB / OBJ maillage (Poisson) | surface reconstruite | **écarté en l'état** | approximation à défauts visibles, pas plus compacte que le PLY |

### Limites communes

- Trois objets, une vue chacun (la caméra d'Idol) ; Three.js comme seul lecteur tiers testé.
- Le rendu de référence est Spark : un écart mesure la différence avec ce que montre le Studio, pas une
  vérité absolue.
