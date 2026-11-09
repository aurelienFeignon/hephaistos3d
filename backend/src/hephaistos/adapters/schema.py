"""Sous-ensemble JSON Schema que le formulaire générique sait rendre (architecture.md §7).

Un adaptateur dont le modèle de paramètres en sort casse le formulaire en silence : ce contrôle le
refuse au test, avant toute interface.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ValidationError

from hephaistos.adapters.base import AdapterError, ModelAdapter
from hephaistos.domain.errors import ErrorCode

TYPES = {"integer", "number", "boolean", "string"}
TOP_KEYS = {"type", "title", "description", "properties", "required", "additionalProperties"}
FIELD_KEYS = {"type", "title", "description", "default", "enum", "minimum", "maximum", "multipleOf", "anyOf", "x-ui"}
NULLABLE_BRANCH_KEYS = {"type", "enum", "minimum", "maximum", "multipleOf"}
WIDGETS = {"slider", "number", "select", "radio", "toggle"}
X_UI_REQUIRED = {"widget", "order"}
X_UI_KEYS = X_UI_REQUIRED | {"group", "advanced", "unit", "labels"}


def schema_violations(schema: dict[str, Any]) -> list[str]:
    """Écarts au sous-ensemble ; liste vide si le schéma est rendable."""
    problems = [f"mot-clé de premier niveau hors sous-ensemble : {key}" for key in schema.keys() - TOP_KEYS]
    if schema.get("type") != "object":
        problems.append("le schéma doit être un objet")
    if schema.get("additionalProperties") is not False:
        problems.append("additionalProperties doit valoir false (extra=forbid)")
    orders: dict[int, str] = {}
    for name, prop in schema.get("properties", {}).items():
        problems += [f"{name} : {problem}" for problem in _field_violations(prop)]
        order = prop.get("x-ui", {}).get("order")
        if isinstance(order, int):
            if order in orders:
                problems.append(f"{name} : order {order} déjà pris par {orders[order]}")
            orders[order] = name
    return problems


def _field_violations(prop: dict[str, Any]) -> list[str]:
    problems = [f"mot-clé hors sous-ensemble : {key}" for key in prop.keys() - FIELD_KEYS]
    if "anyOf" in prop:
        problems += _nullable_violations(prop["anyOf"])
    elif prop.get("type") not in TYPES:
        problems.append(f"type {prop.get('type')!r} hors sous-ensemble")
    problems += _x_ui_violations(prop.get("x-ui"))
    problems += _label_violations(prop)
    return problems


def _nullable_violations(branches: list[dict[str, Any]]) -> list[str]:
    """Seule forme admise : une valeur simple OU null (champ facultatif)."""
    values = [branch for branch in branches if branch != {"type": "null"}]
    if len(branches) != 2 or len(values) != 1:
        return ["anyOf n'est admis que pour « valeur ou null »"]
    branch = values[0]
    problems = [f"mot-clé hors sous-ensemble dans anyOf : {key}" for key in branch.keys() - NULLABLE_BRANCH_KEYS]
    if branch.get("type") not in TYPES:
        problems.append(f"type {branch.get('type')!r} hors sous-ensemble dans anyOf")
    return problems


def _label_violations(prop: dict[str, Any]) -> list[str]:
    """Les libellés traduisent des valeurs d'enum : une clé inconnue n'afficherait jamais rien."""
    x_ui = prop.get("x-ui")
    if not isinstance(x_ui, dict) or "labels" not in x_ui:
        return []
    labels = x_ui["labels"]
    if not isinstance(labels, dict) or not all(isinstance(text, str) for text in labels.values()):
        return ["x-ui : labels doit associer un texte à chaque valeur"]
    branches = [branch for branch in prop.get("anyOf", [prop]) if branch != {"type": "null"}]
    known = {str(value) for branch in branches for value in branch.get("enum", [])}
    return [f"x-ui : labels vise une valeur inconnue : {key}" for key in labels.keys() - known]


def _x_ui_violations(x_ui: Any) -> list[str]:
    if not isinstance(x_ui, dict):
        return ["x-ui absent : le formulaire ne saurait ni le placer ni le rendre"]
    problems = [f"x-ui : clé manquante {key}" for key in X_UI_REQUIRED - x_ui.keys()]
    problems += [f"x-ui : clé inconnue {key}" for key in x_ui.keys() - X_UI_KEYS]
    if "widget" in x_ui and x_ui["widget"] not in WIDGETS:
        problems.append(f"x-ui : widget {x_ui['widget']!r} inconnu")
    if "order" in x_ui and not isinstance(x_ui["order"], int):
        problems.append("x-ui : order doit être un entier")
    return problems


def effective_params(adapter: ModelAdapter, preset: str | None, overrides: Mapping[str, Any]) -> BaseModel:
    """Défauts ⊕ préréglage ⊕ saisie, validés : la valeur complète qu'on enregistre et qu'on rejoue."""
    values: dict[str, Any] = {}
    if preset is not None:
        if preset not in adapter.presets:
            known = ", ".join(adapter.presets)
            raise AdapterError(ErrorCode.PARAMS_INVALID, f"préréglage {preset!r} inconnu (connus : {known})")
        values.update(adapter.presets[preset].values)
    values.update(overrides)
    try:
        return adapter.params_model.model_validate(values)
    except ValidationError as exc:
        fields = "; ".join(f"{'.'.join(map(str, e['loc']))} : {e['msg']}" for e in exc.errors())
        raise AdapterError(ErrorCode.PARAMS_INVALID, fields) from exc
