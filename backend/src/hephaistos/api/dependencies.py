"""Shared dependency boundary for the single-principal MVP."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Principal:
    id: str


def current_principal() -> Principal:
    return Principal("local")
