# ADR-0003 — Cycle de vie du modèle : superviseur et processus d'inférence

- **Statut** : Accepté (2026-11-02) — révisé avant acceptation ; option B confirmée
- **Date** : 2026-11-02

## Contexte
Idol tient ~4 Go de poids et annonce 7,2 Go de pic ; son chargement lit ~6,8 Go et la première
génération compile. La carte peut être partagée avec d'autres charges de l'utilisateur (ADR-0011).
Un contexte CUDA ne se libère qu'à la sortie du processus qui l'a créé. Une erreur CUDA peut laisser le contexte inutilisable.

## Décision
- Chaque worker comprend un **superviseur** sans contexte CUDA (file, contrôle NVML à l'admission, suspension) et
  un **processus d'inférence** (`multiprocessing` en `spawn`) qui charge le modèle.
- Le processus d'inférence est créé au premier job éligible, conservé entre les jobs, et **terminé**
  à l'inactivité (`MODEL_IDLE_UNLOAD_SECONDS`), à l'arrêt du worker, ou après toute erreur CUDA.
- Le superviseur peut toujours le tuer (`SIGKILL`) : c'est la garantie de libération en temps borné
  quand il ne répond plus (arrêt, erreur CUDA, délai dépassé).
- Le service `worker` est dans le profil Compose `gpu` : on le monte et le démonte à volonté
  (`docker compose --profile gpu up -d worker` / `docker compose stop worker`) sans toucher au
  reste de l'application.

## Conséquences
- Rendre la VRAM, c'est terminer un processus : libération **totale**, contexte compris.
- Une erreur CUDA ne contamine jamais le superviseur ; pas de redémarrage de conteneur.
- Coût : sérialisation des messages (négligeable) et rechargement + compilation après chaque
  libération (mesuré en P0, atténué par le cache Inductor sur volume).

## Alternatives écartées
- **Option A** — un seul processus, modèle chargé tant que le conteneur tourne, contrôle par
  montage/démontage seulement : ~4,5 Go immobilisés dès que le worker est démarré, même inactif ;
  libération par démontage seulement. Écartée le 2026-11-02 au profit de l'option B.
- Processus neuf par job : rechargement systématique, inacceptable au vu du coût de chargement.
