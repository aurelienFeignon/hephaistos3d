from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from ulid import ULID

from hephaistos.persistence.models import Artifact, Attempt, Base, Generation
from hephaistos.storage.deletion import DeletionConflict, delete_generation
from hephaistos.storage.local import LocalStorage, UnsafePath


@pytest.mark.parametrize("identifier", ["../escape", "/tmp/escape", "a" * 26, "", "8" + "0" * 25])
def test_invalid_ids(tmp_path: Path, identifier: str) -> None:
    with pytest.raises(UnsafePath):
        LocalStorage(tmp_path).source_dir(identifier)


def test_paths_and_symlinks(tmp_path: Path) -> None:
    storage = LocalStorage(tmp_path / "data")
    identifier = str(ULID())
    assert storage.source_file(identifier, "JPEG").name == "original.jpg"
    with pytest.raises(UnsafePath):
        storage.source_file(identifier, "../../secret")
    outside = tmp_path / "outside"
    outside.mkdir()
    storage.root.mkdir()
    (storage.root / "results").symlink_to(outside, target_is_directory=True)
    with pytest.raises(UnsafePath):
        storage.result_dir(identifier)


def test_promotion_is_complete_and_does_not_overwrite(tmp_path: Path) -> None:
    storage = LocalStorage(tmp_path)
    identifier, attempt = str(ULID()), str(ULID())
    staged = storage.attempt_dir(identifier, attempt)
    staged.mkdir(parents=True)
    for name in ["splat.ply", "preview.png", "camera.json"]:
        (staged / name).write_bytes(b"complete")
    destination = storage.promote_results(identifier, attempt)
    assert not staged.exists()
    assert sorted(p.name for p in destination.iterdir()) == ["camera.json", "preview.png", "splat.ply"]
    staged.mkdir()
    (staged / "splat.ply").write_bytes(b"other")
    with pytest.raises(FileExistsError):
        storage.promote_results(identifier, attempt)
    assert (destination / "splat.ply").read_bytes() == b"complete"


def test_incomplete_or_unsafe_promotion_publishes_nothing(tmp_path: Path) -> None:
    storage = LocalStorage(tmp_path)
    identifier, attempt = str(ULID()), str(ULID())
    staged = storage.attempt_dir(identifier, attempt)
    staged.mkdir(parents=True)
    (staged / "splat.ply").write_bytes(b"ply")
    with pytest.raises(ValueError):
        storage.promote_results(identifier, attempt)
    assert not storage.result_dir(identifier).exists()
    (staged / "preview.png").symlink_to(staged / "splat.ply")
    (staged / "camera.json").write_bytes(b"{}")
    with pytest.raises(UnsafePath):
        storage.promote_results(identifier, attempt)
    assert not storage.result_dir(identifier).exists()


def test_source_promotion(tmp_path: Path) -> None:
    storage = LocalStorage(tmp_path)
    identifier = str(ULID())
    staged = storage.upload_dir(identifier)
    staged.mkdir(parents=True)
    (staged / "original.png").write_bytes(b"original")
    (staged / "input.png").write_bytes(b"normalized")
    result = storage.promote_sources(identifier, "PNG")
    assert (result / "input.png").read_bytes() == b"normalized"
    assert not staged.exists()


@pytest.mark.parametrize("status", ["PENDING", "QUEUED", "RUNNING", "COMPLETED", "FAILED", "CANCELLED"])
def test_deletion_recovery(tmp_path: Path, status: str) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    # Exercise deletion without relying on connection-specific FK settings.
    Base.metadata.create_all(engine)
    sessions = sessionmaker(engine)
    storage = LocalStorage(tmp_path / "data")
    identifier = str(ULID())
    now = datetime.now(UTC)
    with sessions.begin() as session:
        session.add(
            Generation(
                id=identifier,
                model_id="fake",
                model_version="1",
                status=status,
                params={},
                seed=1,
                source_filename="../../display.png",
                source_mime="image/png",
                source_width=1,
                source_height=1,
                source_sha256="a" * 64,
                source_has_alpha=False,
                created_at=now,
                max_retries=3,
                protected=True,
            )
        )
        session.flush()
        session.add(
            Artifact(
                generation_id=identifier,
                kind="SPLAT_PLY",
                relative_path="ignored",
                mime="application/octet-stream",
                size_bytes=1,
                sha256="b" * 64,
                created_at=now,
            )
        )
        session.add(
            Attempt(
                generation_id=identifier,
                number=1,
                trigger="INITIAL",
                worker_id="fake",
                started_at=now,
                log_path="ignored",
            )
        )
    source = storage.source_dir(identifier)
    source.mkdir(parents=True)
    (source / "input.png").write_bytes(b"original")
    if status == "RUNNING":
        with pytest.raises(DeletionConflict):
            delete_generation(sessions, storage, identifier, now=now)
        assert source.exists()
        with sessions() as session:
            assert session.get(Generation, identifier).deleting_at is None
    else:
        with patch.object(LocalStorage, "remove_generation", side_effect=PermissionError("injected")):
            with pytest.raises(PermissionError):
                delete_generation(sessions, storage, identifier, now=now)
        with sessions() as session:
            assert session.get(Generation, identifier).deleting_at is not None
            assert session.scalar(select(Artifact)) is not None
        # Simulate a partially completed deletion, then resume.
        (source / "input.png").unlink()
        assert delete_generation(sessions, storage, identifier, now=now)
        assert not source.exists()
        with sessions() as session:
            assert session.get(Generation, identifier) is None
            assert session.scalar(select(Artifact)) is None
            assert session.scalar(select(Attempt)) is None
        assert not delete_generation(sessions, storage, identifier, now=now)
    engine.dispose()


def test_atomic_nested_writers_and_failure_keep_complete_file(tmp_path: Path) -> None:
    from hephaistos.storage.atomic import write_atomically

    destination = tmp_path / "output"

    def outer(handle):
        handle.write(b"outer")
        write_atomically(destination, lambda inner: inner.write(b"inner"))

    write_atomically(destination, outer)
    assert destination.read_bytes() == b"outer"

    def broken(handle):
        handle.write(b"partial")
        raise RuntimeError("injected")

    with pytest.raises(RuntimeError):
        write_atomically(destination, broken)
    assert destination.read_bytes() == b"outer"
    assert list(tmp_path.iterdir()) == [destination]


def test_deletion_unlinks_nested_symlink_without_following_it(tmp_path: Path) -> None:
    storage = LocalStorage(tmp_path / "data")
    identifier = str(ULID())
    source = storage.source_dir(identifier)
    source.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "keep"
    sentinel.write_bytes(b"keep")
    (source / "link").symlink_to(outside, target_is_directory=True)
    storage.remove_generation(identifier)
    storage.remove_generation(identifier)
    assert sentinel.read_bytes() == b"keep"


def test_promotion_across_mounts_and_copy_failure(tmp_path: Path) -> None:
    import errno

    storage = LocalStorage(tmp_path)
    identifier, attempt = str(ULID()), str(ULID())
    staged = storage.attempt_dir(identifier, attempt)
    staged.mkdir(parents=True)
    for name in ["splat.ply", "preview.png", "camera.json"]:
        (staged / name).write_bytes(b"complete")
    original_rename = Path.rename

    def cross_mount(path, target):
        if path == staged:
            raise OSError(errno.EXDEV, "cross mount")
        return original_rename(path, target)

    with patch.object(Path, "rename", cross_mount):
        with patch("hephaistos.storage.local.shutil.copyfile", side_effect=OSError("disk full")):
            with pytest.raises(OSError, match="disk full"):
                storage.promote_results(identifier, attempt)
        assert staged.exists()
        assert not storage.result_dir(identifier).exists()
        assert list(storage.result_dir(identifier).parent.iterdir()) == []
        destination = storage.promote_results(identifier, attempt)
    assert (destination / "splat.ply").read_bytes() == b"complete"
    assert staged.exists()  # Cross-mount staging is later removed by the janitor.
