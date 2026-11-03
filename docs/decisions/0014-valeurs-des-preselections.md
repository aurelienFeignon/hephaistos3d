# ADR-0014 — Valeurs des préréglages d'Idol

- **Statut** : Accepté (2026-11-03, P1.10)
- **Date** : 2026-11-03

## Contexte
Les éditeurs d'Idol ne publient qu'un jeu de valeurs (50 pas, guidage 3,0, décalage 3,0,
262 144 gaussiennes). Rapide et Qualité étaient des hypothèses (`specifications.md` §3.3), à
retenir seulement si : Rapide ≤ 60 % du temps d'Équilibré sur les images de référence, et Qualité
tient sous `VRAM totale − 1,5 Go` (repli à 524 288 gaussiennes sinon).

## Mesure
`hephaistos.probes.presets`, RTX 5070 12 Go (12 227 Mio), mode `full`, dans l'image `worker-test` ;
un seul processus d'inférence, une génération de chauffe (compilation, 67 s) puis 3 images
(nb-armchair et fig-explorer en `synthetic`, photo-exif6 en `real`) × 3 préréglages × 2 répétitions.
Rapport brut : `var/probe/presets/presets-1793725453.json`.

| Préréglage | Valeurs | Médiane | Rapport à Équilibré | Pic NVML d'Hephaistos |
|---|---|---|---|---|
| Rapide | `steps=25, num_gaussians=131072` | 22,1 s | 0,511 · 0,514 · 0,506 | 7 455 Mio |
| Équilibré | `steps=50, cfg=3.0, rescale_t=3.0, num_gaussians=262144` | 43,3 s | — | 7 624 Mio |
| Qualité | `steps=50, num_gaussians=1048576` | 46,2 s | — | 7 624 Mio |

Budget de Qualité : 12 227 − 1 536 = 10 691 Mio. Appels au débruiteur = appels attendus dans les
18 générations.

## Décision
- Les trois préréglages sont retenus **tels quels**. Qualité garde 1 048 576 gaussiennes : le repli
  à 524 288 n'est pas nécessaire.
- Le besoin déclaré d'Idol (`vram_required_bytes`) devient le pic **NVML** mesuré, 7 624 Mio,
  au lieu de `max_memory_reserved` (6,84 Gio) : le contexte CUDA et les noyaux compilés sont hors de
  l'allocateur de torch et comptent quand même.

## Conséquences
- Le pic ne dépend presque pas du préréglage : il vient des poids et de l'échantillonnage, pas du
  nombre de gaussiennes. Le seuil d'admission par défaut (`GPU_MIN_FREE_VRAM_MB=8192`) couvre les
  trois ; un test le vérifie.
- Qualité ne coûte que ~7 % de temps de plus qu'Équilibré : le décodage de 4× plus de gaussiennes
  est bref devant l'échantillonnage.
- Mesures valables pour sm_120 en mode `full` ; une autre carte ou un autre mode se remesure avec la
  même sonde.

## Alternatives écartées
- Rapide à 10 pas (borne basse) : plus rapide encore, mais rien ne justifiait de s'éloigner autant
  des valeurs publiées alors que 25 pas tiennent déjà le critère.
- Qualité à 100 pas : doublerait la durée sans critère de qualité visuelle mesurable à ce stade.
