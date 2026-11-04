from pathlib import Path

import pytest
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

from alembic import command
from hephaistos.persistence.models import Base


def test_migration_roundtrip(tmp_path: Path) -> None:
    config = Config("alembic.ini")
    url = f"sqlite:///{tmp_path / 'test.db'}"
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "head")
    engine = create_engine(url)
    with engine.connect() as connection:
        assert set(inspect(connection).get_table_names()) == set(Base.metadata.tables) | {"alembic_version"}
        assert compare_metadata(MigrationContext.configure(connection), Base.metadata) == []
        with pytest.raises(IntegrityError):
            connection.execute(
                text("INSERT INTO system_state (id, dispatch_paused, updated_at) VALUES (2, 0, CURRENT_TIMESTAMP)")
            )
        connection.rollback()
        connection.execute(
            text("INSERT INTO system_state (id, dispatch_paused, updated_at) VALUES (1, 0, CURRENT_TIMESTAMP)")
        )
        connection.commit()
        with pytest.raises(IntegrityError):
            connection.execute(text("UPDATE system_state SET paused_by = 'invalid'"))
        connection.rollback()
    command.downgrade(config, "base")
    assert inspect(engine).get_table_names() == ["alembic_version"]
    command.upgrade(config, "head")
    engine.dispose()


@pytest.mark.parametrize(
    "table,column,values",
    [
        ("generation", "status", ["PENDING", "QUEUED", "RUNNING", "COMPLETED", "FAILED", "CANCELLED"]),
        ("attempt", "trigger", ["INITIAL", "AUTO_RETRY", "MANUAL_RETRY", "RECOVERY", "RESUME"]),
        ("attempt", "outcome", ["SUCCEEDED", "FAILED", "ABANDONED", "INTERRUPTED", "CANCELLED"]),
        ("artifact", "kind", ["SPLAT_PLY", "PREVIEW_PNG", "CAMERA_JSON"]),
        ("worker", "state", ["NO_GPU", "IDLE", "LOADING", "READY", "BUSY", "WAITING_GPU", "DEGRADED", "STOPPING"]),
        ("system_state", "paused_by", ["user", "config", "system"]),
    ],
)
def test_enum_checks(tmp_path: Path, table: str, column: str, values: list[str]) -> None:
    config = Config("alembic.ini")
    url = f"sqlite:///{tmp_path / 'constraints.db'}"
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "head")
    engine = create_engine(url)
    with engine.begin() as connection:
        # Populate all required fields directly: database constraints must protect non-ORM callers too.
        columns = inspect(connection).get_columns(table)
        payload = {}
        for col in columns:
            if col["name"] == column or col["nullable"] or col["default"] is not None:
                continue
            kind = str(col["type"])
            payload[col["name"]] = (
                1
                if "INT" in kind or "BOOL" in kind
                else "2026-01-01 00:00:00"
                if "DATE" in kind
                else "{}"
                if "JSON" in kind
                else "0" * 26
            )
        payload[column] = values[0]
        # The other enum on attempt must also be valid.
        if table == "attempt":
            payload["trigger"] = "INITIAL"
        names = ", ".join(f'"{name}"' for name in payload)
        binds = ", ".join(f":{name}" for name in payload)
        connection.execute(text(f'INSERT INTO "{table}" ({names}) VALUES ({binds})'), payload)
        for value in values:
            connection.execute(text(f'UPDATE "{table}" SET "{column}" = :value'), {"value": value})
        with pytest.raises(IntegrityError), connection.begin_nested():
            connection.execute(text(f'UPDATE "{table}" SET "{column}" = :value'), {"value": "invalid"})
    engine.dispose()


def test_ulids_and_cascade() -> None:
    from datetime import UTC, datetime

    from sqlalchemy import delete, select
    from sqlalchemy.orm import Session

    from hephaistos.persistence.models import Artifact, Generation

    engine = create_engine("sqlite://")
    with engine.connect() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        generation = Generation(
            model_id="fake",
            model_version="1",
            status="PENDING",
            params={},
            seed=42,
            source_filename="image.png",
            source_mime="image/png",
            source_sha256="a" * 64,
            source_width=10,
            source_height=10,
            source_has_alpha=False,
            created_at=datetime.now(UTC),
            max_retries=2,
        )
        session.add(generation)
        session.flush()
        assert len(generation.id) == 26
        assert generation.attempt_count == 0
        assert generation.protected is False
        assert generation.needs_diagnosis is False
        artifact = Artifact(
            generation_id=generation.id,
            kind="SPLAT_PLY",
            relative_path="result.ply",
            mime="application/octet-stream",
            size_bytes=2**33,
            sha256="b" * 64,
            metadata_={"gaussians": 262144},
            created_at=datetime.now(UTC),
        )
        session.add(artifact)
        session.commit()
        assert artifact.id != generation.id
        assert artifact.size_bytes == 2**33
        assert artifact.metadata_ == {"gaussians": 262144}
        session.execute(delete(Generation).where(Generation.id == generation.id))
        session.commit()
        assert session.scalar(select(Artifact)) is None
    engine.dispose()
