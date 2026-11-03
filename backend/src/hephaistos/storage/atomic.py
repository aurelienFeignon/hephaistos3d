"""Écriture atomique : un fichier final existe entier ou n'existe pas (annulation, OOM, mort du processus)."""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from typing import BinaryIO


def write_atomically(dest: Path, write: Callable[[BinaryIO], object]) -> Path:
    tmp = dest.with_name(f".{dest.name}.tmp")
    try:
        with tmp.open("wb") as handle:
            write(handle)
        os.replace(tmp, dest)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return dest
