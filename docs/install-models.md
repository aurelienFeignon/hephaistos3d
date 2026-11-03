# Installation des poids — procédure manuelle (P0.4)

> ✅ **Exécuté le 2026-11-03** (accès confirmés, 118 Go libres) : 6 fichiers `.safetensors`, sha256
> tous conformes aux métadonnées du Hub, `refs/main` écrits à la main (voir §3).

Volume total : **≈ 6,8 Go** sous `/opt/hephaistos3d/var/models/` (D4).

## 1. Demandes d'accès (avec votre compte Hugging Face)

État vérifié le 2026-11-02 par `auth_check` avec le jeton local (lecture seule) :

| Dépôt | Accès | Licence | Type d'approbation |
|---|---|---|---|
| `speridlabs/idol` | ✅ ouvert | Apache-2.0 | aucune |
| `facebook/dinov3-vith16plus-pretrain-lvd1689m` | ❌ **non accordé** | DINOv3 License (Meta) | **manuelle**, délai inconnu |
| `black-forest-labs/FLUX.2-dev` | ❌ **non accordé** | FLUX Non-Commercial License | automatique après acceptation |

Gestes :

1. Ouvrir https://huggingface.co/facebook/dinov3-vith16plus-pretrain-lvd1689m, connecté à
   votre compte Hugging Face, lire la licence, remplir le formulaire d'accès. **À faire en premier** : c'est le
   chemin critique du Gate P0.
2. Ouvrir https://huggingface.co/black-forest-labs/FLUX.2-dev, lire la licence non commerciale
   (D7 : usage personnel), accepter. Seul le sous-dossier `vae/` sera téléchargé.
3. Vérifier, sans rien télécharger :

```bash
uvx --from 'huggingface_hub>=0.34' python -I -c "
from huggingface_hub import auth_check
for r in ['facebook/dinov3-vith16plus-pretrain-lvd1689m', 'black-forest-labs/FLUX.2-dev']:
    try: auth_check(r); print('ACCÈS OK', r)
    except Exception as e: print('NON ACCORDÉ', r, type(e).__name__)
"
```

## 2. Contrôle de l'espace disque

```bash
df -h /opt/hephaistos3d
```

Besoin : 6,8 Go de poids + ~12 Go pour l'image `worker` (torch CUDA) + caches de compilation. Exiger
**au moins 50 Go libres** avant de lancer : des reconstructions d'images Docker peuvent consommer
plusieurs dizaines de Go en une heure (constaté sur la machine de test).

## 3. Téléchargement (après accord explicite)

Révisions épinglées au 2026-11-02 :

| Dépôt | Révision |
|---|---|
| `speridlabs/idol` | `2731c2291fc65a797c5cb627a5e5526fc1490902` |
| `facebook/dinov3-vith16plus-pretrain-lvd1689m` | `c807c9eeea853df70aec4069e6f56b28ddc82acc` |
| `black-forest-labs/FLUX.2-dev` | `26afe3a78bb242c0a8bb181dcc8937bb16e5c66c` |

```bash
cd /opt/hephaistos3d
HF="uvx --from huggingface_hub==2.4.0 hf"

# Poids Idol : dossier local, lu directement par IdolPipeline(weights_dir)
$HF download speridlabs/idol \
  config.json denoiser.safetensors decoder.safetensors decoder_gs.safetensors birefnet.safetensors \
  --revision 2731c2291fc65a797c5cb627a5e5526fc1490902 \
  --local-dir var/models/idol

# Encodeurs : dans un CACHE Hugging Face, car Idol les désigne par identifiant de dépôt.
# ⛔ Déplacer HF_HOME déplace aussi l'emplacement du jeton : sans HF_TOKEN explicite, la requête part
# sans authentification et DINOv3 répond « requires approval » (mesuré le 2026-11-03).
export HF_TOKEN="$(cat ~/.cache/huggingface/token)"
export HF_HOME=/opt/hephaistos3d/var/models/huggingface
$HF download facebook/dinov3-vith16plus-pretrain-lvd1689m \
  --revision c807c9eeea853df70aec4069e6f56b28ddc82acc
$HF download black-forest-labs/FLUX.2-dev --include "vae/*" \
  --revision 26afe3a78bb242c0a8bb181dcc8937bb16e5c66c
unset HF_HOME HF_TOKEN
```

⚠️ En mode hors ligne, `transformers` et `diffusers` résolvent le cache par `refs/main`. Un
téléchargement par `--revision <sha>` **ne crée pas** `refs/main` (constaté le 2026-11-03) : il faut
l'écrire avec la révision épinglée (`printf <sha> > .../refs/main`). À vérifier après téléchargement :

```bash
cat var/models/huggingface/hub/models--facebook--dinov3-vith16plus-pretrain-lvd1689m/refs/main
cat var/models/huggingface/hub/models--black-forest-labs--FLUX.2-dev/refs/main
```

## 4. Vérification hors ligne (sans GPU)

```bash
# empreintes et tailles attendues : idol-feasibility.md §5
ls -la var/models/idol
du -sh var/models/idol var/models/huggingface
```

Le jeton HF ne sert qu'ici, sur l'hôte. Aucun conteneur ne le reçoit.
