"""Normalisation d'une image téléversée en PNG d'inférence (P1.4, architecture.md §11).

Le format se lit au décodage, jamais à l'extension. Le plafond de pixels est contrôlé AVANT le
décodage complet : un en-tête suffit à refuser une bombe de décompression. L'orientation EXIF est
appliquée (Idol l'ignore), puis toutes les métadonnées sont abandonnées.
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps, UnidentifiedImageError

ACCEPTED_FORMATS = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}
_SIXTEEN_BIT_MODES = {"I;16", "I;16B", "I;16L", "I;16N", "I"}


class RejectReason(StrEnum):
    UNSUPPORTED_FORMAT = "unsupported_format"  # HTTP 415
    UNDECODABLE = "undecodable"  # HTTP 415
    TOO_MANY_PIXELS = "too_many_pixels"  # HTTP 422


class ImageRejected(Exception):
    def __init__(self, reason: RejectReason, detail: str) -> None:
        super().__init__(f"{reason}: {detail}")
        self.reason = reason
        self.detail = detail


@dataclass(frozen=True)
class ImageInfo:
    width: int
    height: int
    has_alpha: bool  # vrai seulement si le canal alpha porte au moins un pixel non opaque
    source_format: str
    source_mime: str


def normalize_image(src: Path, dest: Path, *, max_pixels: int) -> ImageInfo:
    try:
        with Image.open(src) as image:
            fmt = image.format or ""
            if fmt not in ACCEPTED_FORMATS:
                raise ImageRejected(RejectReason.UNSUPPORTED_FORMAT, f"format {fmt or 'inconnu'}")
            width, height = image.size
            if width * height > max_pixels:
                raise ImageRejected(RejectReason.TOO_MANY_PIXELS, f"{width} x {height} > {max_pixels} pixels")
            image.load()
            normalized = _to_rgb_or_rgba(ImageOps.exif_transpose(image))
    except Image.DecompressionBombError as exc:
        raise ImageRejected(RejectReason.TOO_MANY_PIXELS, str(exc)) from exc
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError) as exc:
        raise ImageRejected(RejectReason.UNDECODABLE, str(exc)) from exc

    has_alpha = normalized.mode == "RGBA"
    _write_png_atomically(normalized, dest)
    return ImageInfo(normalized.width, normalized.height, has_alpha, fmt, ACCEPTED_FORMATS[fmt])


def _to_rgb_or_rgba(image: Image.Image) -> Image.Image:
    if image.mode == "P":
        image = image.convert("RGBA" if "transparency" in image.info else "RGB")
    elif image.mode in _SIXTEEN_BIT_MODES:
        # convert() tronquerait les valeurs 16 bits à 255 : on remet l'échelle sur 8 bits.
        levels = np.clip(np.asarray(image, dtype=np.float64) / 257.0, 0, 255)
        image = Image.fromarray(levels.round().astype(np.uint8))
    if image.mode in {"RGBA", "LA", "PA", "La", "RGBa"}:
        image = image.convert("RGBA")
        alpha_min, _ = image.getchannel("A").getextrema()
        if alpha_min == 255:  # alpha entièrement opaque : il ne porte aucune information
            image = image.convert("RGB")
    elif image.mode != "RGB":
        image = image.convert("RGB")
    clean = Image.new(image.mode, image.size)  # aucune métadonnée héritée (EXIF, ICC, texte)
    clean.paste(image)
    return clean


def _write_png_atomically(image: Image.Image, dest: Path) -> None:
    fd, tmp = tempfile.mkstemp(dir=dest.parent, prefix=f".{dest.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            image.save(handle, "PNG")
        os.replace(tmp, dest)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
