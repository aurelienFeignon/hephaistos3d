# Audit de faisabilité — Idol SDK sur RTX 5070

> Statut : **audit documentaire terminé, aucune exécution**. Aucun poids n'a été téléchargé,
> aucune inférence n'a été lancée. Tout ce qui est marqué **[À MESURER P0]** est une hypothèse
> tant que le Gate P0 ne l'a pas démontré sur la machine de test.

Date de l'audit : 2026-11-02. Complété le même jour après validation de l'architecture (§3.1,
§4.1, §5.1, §7 révisé, §11) ; §4.1 et §11 révisés le 2026-11-03 (ADR-0011).

## 1. Objet audité

| Élément | Valeur |
|---|---|
| Dépôt | https://github.com/speridlabs/idol-sdk |
| Commit audité | `4702a7f` « initial commit », 2026-10-29 21:09 +0100 — **seul commit du dépôt** |
| Version déclarée | `idol 0.1.0` (`pyproject.toml`, `idol/__init__.py`) |
| Taille du code | ~2 050 lignes Python, 15 fichiers, **aucun test**, aucune release, aucune CI |
| Poids | `speridlabs/idol` sur Hugging Face, modifié le 2026-10-29 |

Méthode : clonage en lecture seule (`git clone --depth 50`), lecture intégrale de `idol/`, de `demo/`
et du README ; métadonnées Hugging Face lues via l'API publique (`/api/models/<repo>?blobs=true`) —
tailles et empreintes des fichiers, sans les télécharger.

⚠️ **Le projet a 4 jours.** Il n'existe ni historique, ni politique de versions, ni garantie de
stabilité d'API. Toute intégration **épingle le commit** et traite chaque mise à jour comme une
migration.

## 2. Licences

| Composant | Licence | Accès | Conséquence |
|---|---|---|---|
| Code `idol-sdk` | Apache-2.0 | public | Aucune contrainte. |
| Poids `speridlabs/idol` (DiT, décodeurs, BiRefNet) | Apache-2.0 (carte HF) | public, non gated | Aucune contrainte. |
| `facebook/dinov3-vith16plus-pretrain-lvd1689m` | **DINOv3 License** (Meta, propriétaire) | **gated, approbation MANUELLE** | Usage permis sous conditions (contrôles d'export, exclusions militaires/armes). L'approbation peut prendre du temps : **chemin critique du Gate P0**. |
| `black-forest-labs/FLUX.2-dev` (seul `vae/` est utilisé) | **FLUX Non-Commercial License** | gated, approbation automatique | Usage personnel : compatible. **Tout usage commercial ou hébergé pour des tiers est bloqué** tant que ce VAE vient de ce dépôt. |

Piste de contournement **non vérifiée** : `black-forest-labs/FLUX.2-klein-4B` est Apache-2.0, non gated,
et contient un `vae/`. Mais ses fichiers diffèrent de ceux de FLUX.2-dev :

| | `FLUX.2-dev/vae` | `FLUX.2-klein-4B/vae` |
|---|---|---|
| `diffusion_pytorch_model.safetensors` | 336 213 556 o, sha256 `d64f3a68…` | 168 120 878 o, sha256 `ca70d220…` |
| `config.json` | 770 o | 821 o |

La moitié de taille évoque les mêmes poids en bf16 — et Idol **convertit le VAE en bf16 au chargement**
(`conditioning.py`, `torch_dtype=torch.bfloat16`), donc une substitution pourrait être exacte. C'est
une conjecture : l'égalité tenseur par tenseur après conversion se vérifie, elle ne se suppose pas.
Hors périmètre du MVP (usage personnel) ; consigné pour le jour où la question se poserait.

## 3. Installation et dépendances

`pyproject.toml` (hatchling) :

```
requires-python >= 3.10
torch>=2.5  torchvision>=0.20  transformers>=4.56  diffusers>=0.36
huggingface_hub>=0.34  safetensors>=0.4  tensordict>=0.8
numpy>=1.26  pillow>=10.0  plyfile>=1.0  tqdm>=4.66
extra [demo] : gradio==5.49.1, spaces
```

Constats :

1. **Bornes basses uniquement.** Versions courantes au 2026-11-02 : torch 2.15.0, transformers
   **5.22.1**, diffusers 0.42.1, tensordict 0.15.4. Idol a vraisemblablement été écrit contre
   transformers 4.x : le passage en 5.x a déprécié l'argument `torch_dtype` qu'Idol emploie
   (`AutoModel.from_pretrained(..., torch_dtype=...)`). **[À MESURER P0]** — on fige un jeu de
   versions qui fonctionne dans `uv.lock` et on ne le relâche que par une migration testée.
2. **`diffusers>=0.36`** est requis pour `AutoencoderKLFlux2`.
3. **`flash-attn` est optionnel** : détecté par `importlib.util.find_spec`, sinon `sdpa`. Sur
   Blackwell grand public (sm_120), on **ne l'installe pas** au MVP (compilation longue, support
   incertain) ; `sdpa` donne « le même résultat, plus lent » selon le README.
4. **`torch.compile` (mode `optimize=True`, défaut sur GPU)** a besoin de Triton, qui compile un
   lanceur C à la volée : l'image du worker doit contenir **un compilateur C et les en-têtes Python**
   (`gcc`, `python3-dev`). Une image `-slim` nue échouera au premier appel.
5. Aucune dépendance système exotique : pas d'extension CUDA à compiler (le seul opérateur natif,
   `torchvision.ops.deform_conv2d` pour BiRefNet, est livré dans les wheels torchvision).
6. Python : 3.10+ côté Idol → **Python 3.12** retenu (c'est aussi la version de la démo HF).

### 3.1 Jeu de versions verrouillé (`backend/uv.lock`)

| Paquet | Version | Note |
|---|---|---|
| torch | **2.14.1+cu132** | torch 2.15.0 existe, mais **aucun torchvision n'est publié pour lui** (index cu132 et cu134 vérifiés) |
| torchvision | 0.29.2+cu132 | |
| triton | 3.8.0 | |
| nvidia-cudnn-cu13 | 9.24.0.43 | |
| transformers | 5.22.1 | **majeure 5** |
| diffusers | 0.42.1 | |
| huggingface_hub | 2.4.0 | **majeure 2** |
| tensordict | 0.15.4 | |
| idol | 0.1.0 @ `4702a7f00850e34b6e83a2170cc123c3e9570744` | |

Compatibilité **statique** vérifiée dans ces versions (lecture des sources, sans exécution) :
`huggingface_hub.auth_check`, `get_token`, `errors.GatedRepoError` et `RepositoryNotFoundError`
existent ; `diffusers.AutoencoderKLFlux2` existe ; `transformers` accepte encore `torch_dtype`
(conservé « for BC », avec avertissement de dépréciation) ; `transformers.models.dinov3_vit` existe.
L'exécution reste à prouver au Gate P0.

Image `worker` construite et inspectée sans GPU : drapeaux d'architecture
`sm_75 sm_80 sm_86 sm_90 sm_100 sm_120` — **sm_120 est compilé**.

## 4. Compatibilité GPU — RTX 5070 (Blackwell, sm_120)

Mesuré sur l'hôte le 2026-11-02 :

| Élément | Valeur |
|---|---|
| GPU | NVIDIA GeForce RTX 5070, **compute capability 12.0**, 12 227 MiB |
| Pilote | 615.71.10, CUDA UMD 13.4 |
| Lien | **PCIe Gen4 x4** (eGPU) |
| NVIDIA Container Toolkit | 1.20.1, runtime `nvidia` et CDI (`nvidia.com/gpu=all`) déclarés |
| Docker / Compose | 29.9.2 / v5.6.1 |
| Debian | 13.7, noyau 7.2.11 |
| RAM | 93 Gio |
| Disque `/` | 342 Gio libres (80 % utilisés) |

- sm_120 exige des wheels PyTorch construites avec CUDA ≥ 12.8. Les index officiels publient
  `torch 2.15.0+cu132` et `+cu134` ; le pilote (CUDA 13.4) les accepte. **Pas de blocage attendu.**
- **Trois points restent à démontrer [À MESURER P0]** :
  - `torch.compile` / Triton sur sm_120 avec la version retenue ;
  - le backend d'attention **cuDNN** (`sdpa_cudnn`) que `optimize=True` impose pendant
    l'échantillonnage — s'il manque pour sm_120, repli `optimize=False` ;
  - le pic VRAM réel aux différents nombres de gaussiennes.
- Le lien **x4** ralentit le transfert initial des poids (~4 Go), sans effet notable sur l'inférence.

### 4.1 ⚠️ Le GPU peut être partagé

La carte n'est pas forcément dédiée : l'utilisateur peut y faire tourner d'autres charges
(services d'inférence, entraînements, calculs manuels). Avec un pic annoncé de **7,2 Go** pour
Idol, une carte de 12 Go laisse ~4 Go aux autres pendant une génération : une génération et une
autre charge lourde **alternent**, elles ne cohabitent pas. Conséquences d'architecture
(`architecture.md` §5, ADR-0011) :

- contrôle de VRAM libre **avant** de prendre un job (pas après) ;
- aucune préemption : une génération démarrée va à son terme, sauf annulation ;
- plafond optionnel du processus d'inférence, pour qu'un OOM reste dans Hephaistos ;
- un OOM est une erreur **transitoire** du job (relance avec backoff) ;
- déchargement du modèle après inactivité, pour rendre la VRAM.

Un eGPU peut disparaître du bus après un redémarrage (constaté sur la machine de test) : le worker
doit survivre au démarrage sans GPU visible (état `NO_GPU`, pas de boucle de crash).

## 5. Poids : volumes et procédure

| Dépôt | Fichiers réellement tirés | Taille |
|---|---|---|
| `speridlabs/idol` | `config.json`, `denoiser`, `decoder`, `decoder_gs`, `birefnet` (`.safetensors`) | **3,08 Go** |
| `facebook/dinov3-vith16plus-pretrain-lvd1689m` | `model.safetensors` + config | **3,36 Go** |
| `black-forest-labs/FLUX.2-dev` | `vae/` uniquement | **0,34 Go** |
| **Total** | | **≈ 6,8 Go** |

Mécanique du SDK (`pipeline.py`, `conditioning.py`) :

- `IdolPipeline.from_pretrained(repo_or_dir)` accepte **un dossier local** : les 5 fichiers Idol
  sont lus depuis ce dossier, sans réseau.
- Les deux encodeurs, eux, sont désignés **par identifiant de dépôt HF** dans `config.json`
  (`image_encoders.dinov3`, `image_encoders.flux2_vae`) et chargés par
  `AutoModel.from_pretrained` / `AutoencoderKLFlux2.from_pretrained`. Ils passent donc par le **cache
  Hugging Face**, jamais par le dossier Idol.
- `require_encoder_access` interroge le Hub avant tout chargement, **sauf si `HF_HUB_OFFLINE=1`**.

Procédure retenue (installation manuelle, conforme à l'exigence « aucun téléchargement depuis l'UI ») :

État au 2026-11-02 (`auth_check` en lecture seule) : `speridlabs/idol`
ouvert ; **DINOv3 et FLUX.2-dev non accordés**. Procédure et révisions épinglées :
`install-models.md`.

1. L'utilisateur demande l'accès à DINOv3 (manuel) et accepte les conditions de FLUX.2-dev, avec
   son compte HF.
2. Sur l'hôte, une seule fois : `hf download` des trois dépôts, avec `HF_HOME` pointé sur le volume
   modèles, en limitant FLUX.2-dev à `vae/*` et Idol à ses 5 fichiers.
3. Le worker tourne avec `HF_HOME=/models/huggingface`, `HF_HUB_OFFLINE=1` : **aucun appel réseau,
   aucun jeton dans le conteneur.**

Le jeton HF ne sert qu'au geste d'installation ; il n'entre dans aucun service.

## 6. Interface Python réellement exposée

```python
from idol import IdolPipeline, IdolOutput, Gaussian

pipeline = IdolPipeline(weights_dir, device=None, dinov3_dtype='bf16', optimize=True)
# ou IdolPipeline.from_pretrained(repo_or_dir, **mêmes kwargs)

output: IdolOutput = pipeline(
    image,                 # str (chemin) ou PIL.Image
    mode='synthetic',      # 'synthetic' (rendus, crop 1.2) | 'real' (photos, crop 1.7)
    seed=42,
    steps=50,
    cfg_strength=3.0,
    rescale_t=3.0,
    num_gaussians=262144,  # 32 768 → 1 048 576, arrondi au multiple de 32 inférieur
    crop_margin=None,      # surcharge la marge du mode
    verbose=False,         # active la barre tqdm de l'échantillonneur
)
output.gaussian.save_ply(path)   # PLY 3DGS standard
output.preprocessed_image        # PIL 1024×1024, fond retiré, recadré, composité sur noir
output.camera                    # {'yaw', 'pitch' (rad), 'radius', 'fov' (°)} prédits
```

Méthodes publiques utilisables séparément : `pipeline.preprocess(image, mode, crop_margin)`,
`pipeline.decode(latent, num_gaussians)`. Exception publique : `idol.conditioning.EncoderAccessError`.

### 6.1 Paramètres d'inférence

| Paramètre | Domaine SDK | Bornes de la démo officielle | Coût |
|---|---|---|---|
| `mode` | `synthetic` \| `real` | radio | aucun (change la marge de recadrage) |
| `seed` | int | libre + aléatoire | aucun |
| `steps` | int (non borné) | 10 → 100 | **linéaire** en temps |
| `cfg_strength` | float (≥ 0 utile) | 0 → 10 | 0 supprime la passe non conditionnée |
| `rescale_t` | float | 1 → 6 | aucun |
| `num_gaussians` | 32 768 → 1 048 576, ×32 | 65 536 · 131 072 · 262 144 · 524 288 · 1 048 576 | décodeur + taille du PLY |
| `crop_margin` | float | non exposé | aucun |

Paramètres **de chargement** (pas de requête) : `dinov3_dtype` (`bf16` ; `fp32` = +1,7 Go),
`optimize`, `device`. Ils relèvent de la configuration du worker, pas de l'interface.

Les 94 préréglages de la démo utilisent **tous** `steps=50, cfg=3.0, rescale_t=3.0,
num_gaussians=262 144, mode=synthetic`. Les éditeurs n'ont publié aucun préréglage « rapide » ou
« qualité » : ceux d'Hephaistos sont donc des **hypothèses à calibrer** (`specifications.md` §5).

### 6.2 Entrées

- Tout format que Pillow ouvre ; la CLI filtre `.png .jpg .jpeg .webp .bmp`. JPG, PNG et WebP sont
  donc couverts.
- **Une image RGBA avec un vrai canal alpha court-circuite BiRefNet** : un PNG détouré est utilisé
  tel quel. À exposer comme une capacité (« image déjà détourée »).
- ⚠️ **L'orientation EXIF n'est pas appliquée** (`Image.open` puis `resize`, sans
  `ImageOps.exif_transpose`). Une photo de téléphone arrive tournée de 90°. → Hephaistos normalise
  l'orientation **avant** de passer l'image au SDK.
- Image sans objet détectable → `ValueError('no object found in the image (the alpha channel is
  empty)')` : erreur **déterministe**, à ne jamais relancer.

### 6.3 Sorties

- **PLY 3DGS standard**, un élément `vertex`, 17 propriétés `float32` :
  `x y z nx ny nz f_dc_0..2 opacity scale_0..2 rot_0..3`.
- **Harmoniques sphériques d'ordre 0 seulement** (pas de `f_rest_*`) : couleur indépendante de
  l'angle de vue.
- Repère converti de z-up en **y-up** à l'écriture ; objet dans `[-0.5, 0.5]³`.
- Opacités et échelles bornées avant inversion (pas d'`inf`).
- Taille : 68 o par gaussienne → **17,8 Mo à 262 144**, **71,3 Mo à 1 048 576**.
- Plus `preprocessed_image` (ce que le modèle a vu) et `camera` (caméra prédite de l'image source —
  utilisable comme vue initiale du visualiseur, après vérification du changement de repère).

Aucune sortie maillage (GLB/OBJ) n'est produite par le SDK.

## 7. Progression

Il n'existe **aucun callback** de progression ; seule une barre `tqdm` dans `idol/sampler.py`.

Une progression **réelle**, sans remplacer aucun symbole du SDK, s'obtient par les **hooks de module
PyTorch** (`register_forward_pre_hook`, API publique) — ADR-0005. Vérifié dans le code :

| Appel dans le SDK | Passe par `__call__` ? | Hook utilisable |
|---|---|---|
| `sample_flow` → `model(sample, t, cond)` (débruiteur) | oui, **1 fois par pas** (2 si guidage sans lot) | ✅ pas `i/N` |
| `OctreeProbabilityFixedlenDecoder.sample` → `model(...)` à chaque niveau | oui | ✅ début du décodage |
| `decode` → `self.decoder_gs(anchors, latent)` | oui | ✅ |
| `ImageEncoder` → `self.dinov3(pixel_values=...)` | oui | ✅ début de l'encodage |
| `BiRefNet.remove_background` → **`self.forward(t)`** | **non** | ❌ — d'où `preprocess` borné par l'entrée de DINOv3 |

Les quatre sous-modules accrochés existent et sont des `nn.Module` (vérifié dans l'image `worker`).
Le même hook lève l'exception d'annulation ; le SDK restaure ses réglages globaux dans un `finally`
(`_sampling_settings`). La première proposition — remplacer `idol.sampler.tqdm` — est **abandonnée**
(dépendance à un détail interne).

Pas de pourcentage global : l'interface affiche l'étape, le pas pendant l'échantillonnage et la
durée écoulée.

⚠️ Avec `optimize=True`, **la première génération d'un processus compile** le débruiteur : le
premier pas est beaucoup plus long. Durée **[À MESURER P0]**. ⚠️ Importer torchvision **crée le
cache Inductor à l'import** : sans `TORCHINDUCTOR_CACHE_DIR` inscriptible, un conteneur en lecture
seule plante immédiatement (constaté le 2026-11-02).

## 8. Chargement unique, plusieurs inférences

Oui : `IdolPipeline` est un objet ordinaire, chargé une fois, appelé autant de fois que voulu (la
démo le fait). Le coût de chargement (lecture ~6,8 Go + compilation au premier appel) justifie un
**processus d'inférence persistant** (ADR-0003).

## 9. Concurrence

- **Taille de lot 1** : le bruit initial est tiré avec `batch_size=1` ; aucune API de lot.
- **Non réentrant** : `_sampling_settings` modifie des **globaux de processus**
  (`idol.models.layers.ATTN_BACKEND`, `torch.backends.cuda.matmul.allow_tf32`) et
  `torch.manual_seed` est global. Deux appels concurrents dans un même processus (threads) se
  perturbent.
- ⇒ **un appel à la fois par processus**. Paralléliser revient à lancer plusieurs processus,
  donc plusieurs copies des poids (~4 Go chacune) : impossible à 2 sur 12 Go.
- Reproductibilité : « same seed, same object » (README). TF32, cuDNN et `torch.compile` ne
  garantissent pas l'identité bit à bit ; on promet la **même graine enregistrée**, pas un fichier
  identique.

## 10. Mémoire et performances annoncées

| Composant | Paramètres | VRAM (README) |
|---|---|---|
| DiT | 370,6 M | 0,8–1,0 Go |
| Décodeur octree | 50,8 M | 0,1–0,2 Go |
| Décodeur gaussien | 237,3 M | 0,5–0,9 Go |
| BiRefNet | 220,7 M | 0,44 Go |
| DINOv3 ViT-H+/16 | 840,6 M | 1,68 Go |
| VAE FLUX.2 | 84,1 M | 0,17 Go |
| **Total** | **~1,80 G** | **3,7–4,4 Go de poids, 7,2 Go de pic** |

- Le pic de 7,2 Go est annoncé **sans préciser le nombre de gaussiennes** ; le décodeur croît avec
  lui. Pic à 1 048 576 : **[À MESURER P0]**. Si > ~10 Go, le préréglage « Qualité » redescend à
  524 288.
- Le README affiche « under 2 seconds », mais **son propre tableau** donne 3,4 s au mieux (B300),
  14,1 s sur RTX 5090, 19,1 s sur RTX 4090, 46,2 s sur RTX 3080 (50 pas, 262 144). La RTX 5070
  n'y figure pas ; par interpolation, **ordre de grandeur 25–60 s par génération à chaud**, hors
  compilation. **[À MESURER P0]**.

## 11. Incompatibilités, incertitudes, contournements

| # | Constat | Gravité | Contournement / suite |
|---|---|---|---|
| A1 | Accès DINOv3 sur approbation **manuelle** | **Bloquant potentiel P0** | Demander l'accès dès maintenant. |
| A2 | Licence non commerciale du VAE FLUX.2-dev | Faible (usage personnel) | Documentée ; piste klein-4B non vérifiée. |
| A3 | SDK de 4 jours, 1 commit, sans tests | Moyenne | Épingler `4702a7f` ; test de forme sur les 4 sous-modules accrochés. |
| A4 | Dépendances sans borne haute ; transformers 5 / huggingface_hub 2 | Moyenne | Verrouillées ; compatibilité statique ✅ ; exécution au Gate P0. |
| A5 | `torch.compile` / cuDNN SDPA sur sm_120 non démontrés | Moyenne | Mesure P0 ; repli `optimize=False`. |
| A6 | Pas de callback de progression | Faible | Hooks de module PyTorch (§7) ; filet `SIGKILL`. |
| A7 | Non réentrant, lot de 1 | Structurante | 1 job GPU par processus ; concurrence par processus. |
| A8 | GPU éventuellement partagé avec d'autres charges | Moyenne | Contrôle de VRAM libre à l'admission, plafond optionnel, 0 octet au repos, worker démontable (profil `gpu`), suspension ; pas de préemption (ADR-0011). |
| A9 | Orientation EXIF ignorée | Faible | Normalisation côté Hephaistos. |
| A10 | Pic VRAM à 1 M gaussiennes inconnu | Moyenne | Mesure P0 ; calibrage du préréglage Qualité. |
| A11 | Image du worker : compilateur C requis | Faible | `gcc` + `python3-dev` dans l'image. |
| A12 | Pas d'export maillage | Attendu | GLB/OBJ hors MVP (`architecture.md` §9). |
| A13 | Pas de torchvision pour torch 2.15 | Faible | torch 2.14.1 + torchvision 0.29.2 (cu132). |
| A14 | Le cache Inductor est créé à l'import | Faible | `/cache` inscriptible monté ; à couvrir par le healthcheck. |
| A15 | Accès HF non accordés | **Bloquant P0** | Demandes à faire (`install-models.md`). |

**Aucun blocage technique avéré** à ce stade. Le blocage est **administratif** (A1/A15 : accès
non accordés). Le partage du GPU se règle à l'admission, sans préemption (ADR-0011).
