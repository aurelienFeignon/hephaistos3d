# Registre des décisions d'architecture

| ADR | Titre | Statut |
|---|---|---|
| [0001](0001-structure-du-depot-et-scaffolding.md) | Structure du dépôt et scaffolding | Accepté (2026-11-02) |
| [0002](0002-file-d-attente-en-base.md) | File d'attente portée par la base de données | Accepté (2026-11-02, D1) |
| [0003](0003-cycle-de-vie-du-modele-en-vram.md) | Cycle de vie du modèle : superviseur et processus d'inférence | Accepté (2026-11-02) — révisé avant acceptation ; option B confirmée |
| [0004](0004-interface-d-adaptateur-et-registre.md) | Interface d'adaptateur et registre | Accepté (2026-11-02) |
| [0005](0005-progression-reelle-par-instrumentation.md) | Progression et annulation par hooks de module PyTorch | Accepté (2026-11-02) — révisé avant acceptation (ajustement 6) |
| [0006](0006-visualiseur-spark.md) | Visualiseur Gaussian Splat : Spark | Accepté (2026-11-02) |
| [0007](0007-exports-ply-natif-seul-au-mvp.md) | Exports : PLY natif seul au MVP | Accepté (2026-11-02) |
| [0008](0008-mises-a-jour-par-interrogation.md) | Mises à jour du frontend par interrogation | Accepté (2026-11-02) |
| [0009](0009-exposition-reseau-minimale.md) | Exposition réseau minimale | Accepté (2026-11-02, D5 : localhost d'abord) |
| [0010](0010-poids-installes-hors-ligne.md) | Poids installés à la main, inférence hors ligne | Accepté (2026-11-02, D3/D4) |
| [0011](0011-partage-du-gpu-sans-preemption.md) | Partage du GPU : contrôle à l'admission, sans préemption | Accepté (2026-11-03, D2) |
| [0012](0012-modes-d-execution-idol.md) | Modes d'exécution d'Idol | Accepté (2026-11-02, ajustement 5) ; défaut fixé après G0.5 |
| [0013](0013-erreurs-non-classees-non-relancees.md) | Erreurs non classées : diagnostic avant relance | Accepté (2026-11-02, D8) |

Format : contexte, décision, conséquences, alternatives. Un ADR accepté ne se modifie plus : on en écrit un nouveau qui le remplace. Les ADR 0003 et 0005 ont été révisés **avant** leur acceptation ; l'ADR 0011 a été réécrit avant tout commit, le 2026-11-03, quand la préemption essayée en P0 a été abandonnée.

