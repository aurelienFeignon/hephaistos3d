# ADR-0004 — Interface d'adaptateur et registre

- **Statut** : Accepté (2026-11-02)
- **Date** : 2026-11-02

## Contexte
L'application ne doit pas être couplée à Idol, sans pour autant construire d'abstraction inutile :
un seul adaptateur réel est prévu au MVP.

## Décision
- Protocole `ModelAdapter` : descripteur, modèle de paramètres Pydantic, préréglages,
  `check_installation`, `load`, `generate(input, hooks)`, `unload`, `classify_error`.
- Aucun import de torch au niveau module d'un adaptateur.
- Registre déclaré **en code** (`idol`, `fake`). La découverte porte sur l'installation, constatée
  par chaque worker et publiée dans son heartbeat ; l'API lit ces heartbeats.
- `FakeAdapter` fait partie du produit (tests, démonstration sans GPU), pas seulement des tests.

## Conséquences
- L'API n'a pas besoin du volume modèles ; un worker déporté publie sa disponibilité de la même façon.
- Une suite de conformité commune est jouée sur `FakeAdapter` et `IdolAdapter`.

## Alternatives écartées
- Découverte par points d'entrée (`entry_points`) ou plugins chargés dynamiquement : utile à
  plusieurs adaptateurs tiers, superflu pour un seul.
- Manifeste par modèle dans le volume : redondant avec `check_installation`.
