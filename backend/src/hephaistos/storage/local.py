"""Local layout and atomic directory promotion (architecture §8).

Only service-owned directories belong under root. Callers serialize promotion with
lease validation and database completion (P2.5); this module never grants a lease.
Across mounts, copy into a hidden destination-local directory before the final rename.
The source staging directory then remains available for janitor cleanup.
"""

from __future__ import annotations

import errno
import re
import shutil
import tempfile
from pathlib import Path


class UnsafePath(ValueError):
    """An identifier, filename or symlink violates the managed layout."""


def validate_id(identifier: str) -> str:
    if re.fullmatch(r"[0-7][0-9A-HJKMNP-TV-Z]{25}", identifier) is None:
        raise UnsafePath("Expected a canonical ULID")
    return identifier


class LocalStorage:
    def __init__(self, root: Path) -> None:
        self.root = root.absolute()
        self._check(self.root)

    def _check(self, path: Path) -> Path:
        # Reject even in-root symlinks, including broken ones and root ancestors.
        for component in (path, *path.parents):
            if component.is_symlink():
                raise UnsafePath(f"Symlink in managed path: {component}")
        if not path.resolve().is_relative_to(self.root.resolve()):
            raise UnsafePath("Path leaves storage root")
        return path

    def _directory(self, area: str, identifier: str) -> Path:
        return self._check(self.root / area / validate_id(identifier))

    def free_bytes(self) -> int:
        values = []
        for area in ("db", "sources", "results", "logs", "tmp"):
            path = self._check(self.root / area)
            while not path.exists():
                path = path.parent
            values.append(shutil.disk_usage(path).free)
        return min(values)

    def source_dir(self, identifier: str) -> Path:
        return self._directory("sources", identifier)

    def source_file(self, identifier: str, image_format: str) -> Path:
        return self._check(self.source_dir(identifier) / self._original_name(image_format))

    @staticmethod
    def _original_name(image_format: str) -> str:
        extensions = {"JPEG": "jpg", "PNG": "png", "WEBP": "webp"}
        if image_format not in extensions:
            raise UnsafePath("Unsupported decoded image format")
        return f"original.{extensions[image_format]}"

    def result_dir(self, identifier: str) -> Path:
        return self._directory("results", identifier)

    def log_file(self, identifier: str, number: int) -> Path:
        if isinstance(number, bool) or number < 1:
            raise UnsafePath("Attempt number must be positive")
        return self._check(self._directory("logs", identifier) / f"attempt-{number}.jsonl")

    def upload_dir(self, identifier: str) -> Path:
        return self._check(self._directory("tmp", identifier) / "upload")

    def attempt_dir(self, identifier: str, attempt_id: str) -> Path:
        return self._check(self._directory("tmp", identifier) / validate_id(attempt_id))

    def _promote(self, staged: Path, destination: Path, expected: set[str]) -> Path:
        self._check(staged)
        self._check(destination)
        if destination.exists():
            raise FileExistsError(destination)
        actual = set()
        for child in staged.iterdir():
            self._check(child)
            if not child.is_file():
                raise UnsafePath("Only regular output files may be promoted")
            actual.add(child.name)
        if actual != expected:
            raise ValueError(f"Incomplete or unexpected files: expected {sorted(expected)}, got {sorted(actual)}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            staged.rename(destination)
        except OSError as exc:
            if exc.errno != errno.EXDEV:
                raise
            local_stage = Path(tempfile.mkdtemp(dir=destination.parent, prefix=f".{destination.name}."))
            try:
                for name in sorted(expected):
                    shutil.copyfile(staged / name, local_stage / name)
                local_stage.rename(destination)
            finally:
                if local_stage.exists():
                    shutil.rmtree(local_stage)
        return destination

    def promote_sources(self, identifier: str, image_format: str) -> Path:
        return self._promote(
            self.upload_dir(identifier), self.source_dir(identifier), {self._original_name(image_format), "input.png"}
        )

    def promote_results(self, identifier: str, attempt_id: str) -> Path:
        return self._promote(
            self.attempt_dir(identifier, attempt_id),
            self.result_dir(identifier),
            {"splat.ply", "preview.png", "camera.json"},
        )

    def remove_generation(self, identifier: str) -> None:
        # Check all top-level paths before starting. rmtree does not follow nested symlinks.
        paths = [self._directory(area, identifier) for area in ("sources", "results", "logs", "tmp")]
        for path in paths:
            try:
                shutil.rmtree(path)
            except FileNotFoundError:
                pass
