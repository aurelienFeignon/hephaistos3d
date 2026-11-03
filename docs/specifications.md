# Cahier des charges fonctionnel — Hephaistos 3D

> Statut : validé le 2026-11-02, avec ajustements.

## 1. Objet

Application web auto-hébergée qui transforme une image 2D d'un objet en objet 3D (Gaussian
Splatting) à l'aide de modèles d'IA. Premier moteur : **Idol** (Sperid Labs).

## 2. Contexte d'usage

| Élément | Valeur |
|---|---|
| Hôte | serveur Linux avec GPU NVIDIA (testé : Debian 13, RTX 5070 12 Go, sm_120) ; la carte peut être partagée avec d'autres charges |
| Réseau | `127.0.0.1:3030` au départ ; ouverture au LAN sur décision explicite (D5) |
| GPU | **contrôle à l'admission, sans préemption** (D2, ADR-0011) : Hephaistos ne prend un job que si la VRAM libre atteint `GPU_MIN_FREE_VRAM_MB`, ne cède jamais le GPU en cours de génération, n'y garde rien au repos ; le worker se monte et se démonte à la main (profil `gpu`) |
| Utilisateurs | un seul, sans authentification (décision temporaire) |
| Usage | personnel, non commercial (conditionne la licence du VAE FLUX.2 — `idol-feasibility.md` §2) |

## 3. Périmètre du MVP

### 3.1 Exigences fonctionnelles

| # | Exigence | Critère vérifiable | Phase |
|---|---|---|---|
| F1 | Importer une image JPG, PNG ou WebP | Les trois formats sont acceptés ; GIF, BMP, TIFF, SVG, PDF sont refusés (415) ; un fichier renommé `.png` qui n'est pas une image est refusé | P2/P3 |
| F2 | Prévisualiser l'image | L'image s'affiche avant soumission avec ses dimensions, son poids et la présence d'un canal alpha ; orientation EXIF appliquée | P3 |
| F3 | Sélectionner un modèle | La liste vient de `GET /models` ; un modèle indisponible est visible, grisé, avec sa raison | P3 |
| F4 | Choisir un préréglage | Rapide / Équilibré / Qualité, valeurs affichées | P3 |
| F5 | Modifier les paramètres avancés | Formulaire généré depuis le schéma de l'adaptateur ; valeurs hors bornes refusées côté client **et** serveur | P3 |
| F6 | Soumettre une génération | Une génération `QUEUED` apparaît en moins de 2 s dans la file | P3 |
| F7 | Consulter la file | Ordre, position, motif d'attente (VRAM, worker absent, relance différée) | P3 |
| F8 | Suivre statut, progression, durée | Étape courante, pas `i/N` pendant l'échantillonnage, durée écoulée ; **aucun pourcentage inventé** | P3 |
| F9 | Visualiser le résultat 3D | Splat affiché avec rotation, zoom, déplacement, réinitialisation | P4 |
| F10 | Télécharger les fichiers | PLY natif, aperçu prétraité, `camera.json` | P4 |
| F11 | Historique | Bibliothèque paginée, filtrable par statut et protection | P3 |
| F12 | Relancer une génération échouée | `FAILED` et `CANCELLED` relançables ; historique des tentatives conservé | P2/P3 |
| F13 | Protéger une génération | Une génération protégée n'est jamais supprimée par la rétention (test dédié) | P2/P4 |
| F14 | Supprimer une génération | Fichiers et lignes supprimés ; reprise automatique en cas d'échec partiel | P2/P4 |
| F15 | Consulter erreurs et journaux | Code, message, tentative, journal JSON par tentative | P3 |
| F16 | Annuler une génération | En file : immédiat ; en cours : au prochain pas d'échantillonnage | P2/P3 |
| F17 | Voir l'état du système | Barre supérieure : workers, état du modèle (chargé / en attente / sans GPU / dégradé), VRAM libre, plafond éventuel, état de la distribution, file | P3 |
| F18 | Suspendre les nouvelles générations | Un bouton suspend la distribution : aucune génération ne démarre tant qu'elle l'est ; celle en cours va à son terme ; la reprise relance la file dans l'ordre | P2/P3 |
| F19 | Refus propre faute de VRAM | Une génération dont le besoin dépasse ce qu'Hephaistos peut jamais obtenir est refusée à la soumission avec un message clair ; une génération servable attend avec son motif, sans erreur | P2/P3 |

F16 et F17 découlent du statut `CANCELLED` et de la barre « statut système » ; F18 et F19 des
ajustements 3 et 7 demandés le 2026-11-02.

### 3.2 Paramètres d'Idol exposés

| Paramètre | Libellé | Simple | Avancé | Domaine proposé |
|---|---|---|---|---|
| `mode` | Type d'image (rendu / photo) | ✅ | ✅ | `synthetic` \| `real` |
| `seed` | Graine | — | ✅ | entier 0 → 2³¹−1, ou aléatoire |
| `steps` | Pas d'échantillonnage | — | ✅ | 10 → 100 (bornes de la démo officielle) |
| `cfg_strength` | Guidage | — | ✅ | 0 → 10, pas 0,1 |
| `rescale_t` | Décalage temporel | — | ✅ | 1 → 6, pas 0,1 |
| `num_gaussians` | Nombre de gaussiennes | — | ✅ | 65 536 · 131 072 · 262 144 · 524 288 · 1 048 576 (32 768 ajouté si utile) |
| `crop_margin` | Marge de recadrage | — | ✅ | vide (= selon le type) ou 1,0 → 2,5 |

`dinov3_dtype` et `optimize` sont des réglages de **chargement**, donc de configuration serveur,
pas de l'interface.

### 3.3 Préréglages

Les éditeurs n'ont publié **qu'un** jeu de valeurs (tous les exemples de la démo : 50 pas, guidage
3,0, décalage 3,0, 262 144 gaussiennes). En conséquence :

| Préréglage | Valeurs | Statut |
|---|---|---|
| **Équilibré** | `steps=50, cfg=3.0, rescale_t=3.0, num_gaussians=262144` | défauts officiels du SDK |
| **Rapide** | `steps=25, num_gaussians=131072`, le reste par défaut | ✅ retenu (ADR-0014) : 51 % du temps d'Équilibré |
| **Qualité** | `steps=50, num_gaussians=1048576`, le reste par défaut | ✅ retenu (ADR-0014) : pic 7 624 Mio, budget 10 691 ; repli inutile |

Critère de calibrage (P1) : sur les images de référence, Rapide ≤ 60 % du temps d'Équilibré ;
Qualité tient sous `VRAM totale − 1,5 Go`. Les valeurs retenues et leurs mesures sont consignées
dans un ADR. Le type d'image (`mode`) n'appartient à aucun préréglage : il décrit l'image, pas la
qualité voulue.

## 4. Interface

```
┌──────────────────────────────────────────────────────────────────────────────┐
│ Hephaistos 3D     Studio · Historique     ● worker prêt · VRAM 4,1/12 Go · 2 en file │
├────────────────┬───────────────────────────────────────┬─────────────────────┤
│ Bibliothèque   │                                       │ Modèle  [Idol ▾]    │
│ [filtres]      │                                       │ Image   [aperçu]    │
│ ▣ chaise   ✓   │          visualiseur 3D               │ Type    ◉ rendu ○ photo │
│ ▣ lampe    ⟳   │      (rotation · zoom · pan · ⟲)      │ Préréglage R · É · Q │
│ ▣ renard   ✗   │                                       │ ▸ Avancé            │
│ ▣ ...      🔒  │                                       │ [ Générer ]         │
├────────────────┴───────────────────────────────────────┴─────────────────────┤
│ File : #1 lampe — échantillonnage 23/50 · 0:18  [Annuler]   #2 renard — attend la VRAM │
└──────────────────────────────────────────────────────────────────────────────┘
```

- Priorité ordinateur (≥ 1280 px) ; en dessous de 1024 px, panneaux latéraux repliables ; mobile :
  consultation seulement (bibliothèque + visualiseur), utilisable sans défilement horizontal.
- Détail d'une génération : paramètres effectifs, graine, durées par étape, tentatives, journaux,
  téléchargements, protection, relance, suppression (avec confirmation).

## 5. Exigences non fonctionnelles

| # | Exigence | Critère |
|---|---|---|
| NF1 | Testable sans GPU | La suite de tests standard passe sur une machine sans GPU, avec l'adaptateur factice |
| NF2 | Reprise après redémarrage | Après `docker compose restart` en pleine génération, la génération reprend ou échoue proprement, sans rester `RUNNING` au-delà de 3 × `JOB_LEASE_SECONDS` |
| NF3 | Cohérence stockage | Aucun fichier sans ligne, aucune ligne `COMPLETED` sans artefact, vérifié par la commande de rapprochement |
| NF4 | Exposition | Avec la configuration par défaut, aucun port n'écoute ailleurs que sur `127.0.0.1` |
| NF5 | Validation de configuration | Une variable invalide empêche le démarrage avec un message qui la nomme |
| NF6 | Partage du GPU | Hephaistos ne démarre aucun job tant que la VRAM libre est sous `GPU_MIN_FREE_VRAM_MB` ; avec `GPU_VRAM_LIMIT_MB` > 0, il ne dépasse jamais ce plafond (l'OOM reste dans Hephaistos) ; il rend **toute** sa VRAM (processus d'inférence terminé) après `MODEL_IDLE_UNLOAD_SECONDS`. Limite acceptée : sans plafond, une autre charge qui démarre pendant une génération peut subir un OOM |
| NF10 | Contrôle manuel | `docker compose stop worker` ramène l'usage GPU d'Hephaistos à 0 sans interrompre l'interface ; le remontage reprend la file |
| NF9 | Erreurs CUDA | Après une erreur CUDA, aucune génération ne s'exécute dans le processus qui l'a subie |
| NF7 | Reproductibilité | Une reconstruction à partir des fichiers verrouillés donne les mêmes versions de dépendances |
| NF8 | Journalisation | Logs JSON structurés, champ `generation_id` sur toute ligne liée à une génération |

## 6. Hors périmètre du MVP

- Authentification, multi-utilisateur, quotas.
- Second adaptateur de modèle.
- Téléchargement ou installation de modèles depuis l'interface.
- Exports GLB/OBJ et reconstruction de maillage (étude en P4, livraison éventuelle hors MVP).
- Traitement par lot de plusieurs images en une requête.
- Accès depuis Internet.
