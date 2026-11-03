"""Vérifie qu'un fichier est un PLY 3D Gaussian Splatting tel qu'Idol l'écrit.

Usage : python -m hephaistos.probes.ply FICHIER.ply [--expected-vertices N] [--bound 0.6]
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np
from plyfile import PlyData

# Ordre exact écrit par idol.gaussian.Gaussian.save_ply (commit 4702a7f).
EXPECTED_PROPERTIES = (
    "x",
    "y",
    "z",
    "nx",
    "ny",
    "nz",
    "f_dc_0",
    "f_dc_1",
    "f_dc_2",
    "opacity",
    "scale_0",
    "scale_1",
    "scale_2",
    "rot_0",
    "rot_1",
    "rot_2",
    "rot_3",
)


@dataclass
class PlyReport:
    path: str
    ok: bool
    vertices: int = 0
    problems: list[str] = field(default_factory=list)
    bbox_min: list[float] | None = None
    bbox_max: list[float] | None = None
    size_bytes: int = 0


def check_ply(path: Path, expected_vertices: int | None = None, bound: float = 0.6) -> PlyReport:
    report = PlyReport(path=str(path), ok=False)
    try:
        report.size_bytes = path.stat().st_size
        ply = PlyData.read(str(path))
    except Exception as exc:  # noqa: BLE001 — fichier absent, tronqué ou pas un PLY : tout échec est un verdict
        report.problems.append(f"illisible: {type(exc).__name__}: {exc}")
        return report

    if [e.name for e in ply.elements] != ["vertex"]:
        report.problems.append(f"éléments inattendus: {[e.name for e in ply.elements]}")
        return report

    vertex = ply["vertex"]
    names = tuple(p.name for p in vertex.properties)
    if names != EXPECTED_PROPERTIES:
        report.problems.append(f"propriétés inattendues: {names}")
        return report
    dtypes = {vertex.data.dtype[name].str for name in names}
    if dtypes != {"<f4"}:
        report.problems.append(f"types inattendus: {sorted(dtypes)} (attendu: float32)")

    report.vertices = len(vertex.data)
    if expected_vertices is not None and report.vertices != expected_vertices:
        report.problems.append(f"{report.vertices} sommets, attendu {expected_vertices}")
    if report.vertices == 0:
        report.problems.append("aucun sommet")
        return report

    values = np.stack([vertex.data[name] for name in names], axis=1)
    non_finite = int((~np.isfinite(values)).sum())
    if non_finite:
        report.problems.append(f"{non_finite} valeurs non finies (NaN/inf)")

    xyz = values[:, :3]
    report.bbox_min = [float(v) for v in np.nanmin(xyz, axis=0)]
    report.bbox_max = [float(v) for v in np.nanmax(xyz, axis=0)]
    if np.nanmax(np.abs(xyz)) > bound:
        report.problems.append(f"positions hors de [-{bound}, {bound}]³")

    report.ok = not report.problems
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("paths", nargs="+", type=Path)
    parser.add_argument("--expected-vertices", type=int, default=None)
    parser.add_argument("--bound", type=float, default=0.6)
    args = parser.parse_args(argv)

    reports = [check_ply(p, args.expected_vertices, args.bound) for p in args.paths]
    print(json.dumps([asdict(r) for r in reports], indent=2, ensure_ascii=False))
    return 0 if all(r.ok for r in reports) else 1


if __name__ == "__main__":
    sys.exit(main())
