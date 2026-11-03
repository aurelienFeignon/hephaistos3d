from pathlib import Path

import numpy as np
import pytest
from plyfile import PlyData, PlyElement

from hephaistos.probes.ply import EXPECTED_PROPERTIES, check_ply


def write_ply(path: Path, n: int = 64, names=EXPECTED_PROPERTIES, dtype: str = "f4", xyz_scale: float = 0.5):
    rng = np.random.default_rng(0)
    data = np.empty(n, dtype=[(name, dtype) for name in names])
    for name in names:
        data[name] = rng.uniform(-1, 1, n)
    for axis in ("x", "y", "z"):
        if axis in names:
            data[axis] = rng.uniform(-xyz_scale, xyz_scale, n)
    PlyData([PlyElement.describe(data, "vertex")]).write(str(path))
    return data


def test_valid_ply(tmp_path):
    path = tmp_path / "ok.ply"
    write_ply(path, n=64)
    report = check_ply(path, expected_vertices=64)
    assert report.ok, report.problems
    assert report.vertices == 64


def test_wrong_vertex_count(tmp_path):
    path = tmp_path / "n.ply"
    write_ply(path, n=32)
    report = check_ply(path, expected_vertices=64)
    assert not report.ok
    assert "attendu 64" in report.problems[0]


def test_nan_is_rejected(tmp_path):
    path = tmp_path / "nan.ply"
    data = write_ply(path)
    data["opacity"][3] = np.nan
    PlyData([PlyElement.describe(data, "vertex")]).write(str(path))
    report = check_ply(path)
    assert not report.ok
    assert any("non finies" in p for p in report.problems)


def test_out_of_bounds_positions(tmp_path):
    path = tmp_path / "far.ply"
    write_ply(path, xyz_scale=2.0)
    assert not check_ply(path).ok


@pytest.mark.parametrize("names", [EXPECTED_PROPERTIES[:-1], ("x", "y", "z")])
def test_wrong_schema(tmp_path, names):
    path = tmp_path / "schema.ply"
    write_ply(path, names=names)
    report = check_ply(path)
    assert not report.ok
    assert "propriétés inattendues" in report.problems[0]


def test_float64_is_rejected(tmp_path):
    path = tmp_path / "f8.ply"
    write_ply(path, dtype="f8")
    assert not check_ply(path).ok


def test_not_a_ply(tmp_path):
    path = tmp_path / "fake.ply"
    path.write_bytes(b"%PDF-1.7 not a ply")
    report = check_ply(path)
    assert not report.ok
    assert report.problems[0].startswith("illisible")
