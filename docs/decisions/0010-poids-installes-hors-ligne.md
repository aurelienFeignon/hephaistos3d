# ADR-0010 — Poids installés à la main, inférence hors ligne

- **Statut** : Accepté (2026-11-02, D3/D4)
- **Date** : 2026-11-02

## Contexte
Idol charge ses poids depuis un dossier, mais ses encodeurs (DINOv3, VAE FLUX.2) depuis le cache
Hugging Face, par identifiant de dépôt ; deux dépôts sont gated. Aucune installation ne doit se
faire depuis l'interface.

## Décision
- `var/models/` : `idol/` (5 fichiers) et `huggingface/` (cache HF), montés en lecture seule ; caches de compilation à part dans `var/cache/`.
- Installation manuelle documentée (`hf download` avec `HF_HOME` pointé sur le volume), exécutée par
  l'utilisateur ou avec son accord.
- Worker : `HF_HOME=/models/huggingface`, `HF_HUB_OFFLINE=1`, volume en lecture seule, **aucun jeton HF dans les conteneurs**.

## Conséquences
- Inférence sans réseau, démontrée au Gate P0 (conteneur sans réseau).
- La révision exacte des poids est lisible dans le cache et enregistrée dans `model_version`.

## Alternatives écartées
- Laisser le SDK télécharger au premier appel : exige un jeton dans le conteneur et un accès réseau
  sortant, contraire aux exigences.
