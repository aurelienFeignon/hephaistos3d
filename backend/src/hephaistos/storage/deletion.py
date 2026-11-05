"""Recoverable manual deletion: commit marker, remove files, commit row deletion.

Queue claims/retries must exclude deleting_at != NULL. A failed file removal leaves
both the marker and database rows intact for a later janitor retry. Retention policy
selection belongs to the janitor; manual deletion explicitly permits protected jobs.
"""

from datetime import datetime

from sqlalchemy import delete, update
from sqlalchemy.orm import Session, sessionmaker

from hephaistos.persistence.models import Artifact, Attempt, Generation
from hephaistos.storage.local import LocalStorage, validate_id


class DeletionConflict(Exception):
    """Cancel a RUNNING generation before requesting deletion."""


def delete_generation(
    sessions: sessionmaker[Session],
    storage: LocalStorage,
    identifier: str,
    *,
    now: datetime,
    retention_before: datetime | None = None,
) -> bool:
    """Owns its transactions. Return False for an already absent generation."""
    validate_id(identifier)
    with sessions.begin() as session:
        statement = update(Generation).where(Generation.id == identifier, Generation.status != "RUNNING")
        if retention_before is not None:
            statement = statement.where(
                Generation.status.in_(("COMPLETED", "FAILED", "CANCELLED")),
                Generation.protected.is_(False),
                Generation.finished_at < retention_before,
                Generation.deleting_at.is_(None),
            )
        marked = session.scalar(statement.values(deleting_at=now).returning(Generation.id))
        if marked is None:
            if retention_before is not None or session.get(Generation, identifier) is None:
                return False
            raise DeletionConflict(identifier)
    storage.remove_generation(identifier)
    with sessions.begin() as session:
        generation = session.get(Generation, identifier)
        if generation is None:
            return True
        if generation.status == "RUNNING" or generation.deleting_at is None:
            raise DeletionConflict(identifier)
        # Explicit children also protect callers before engine FK configuration (P2.5).
        session.execute(delete(Artifact).where(Artifact.generation_id == identifier))
        session.execute(delete(Attempt).where(Attempt.generation_id == identifier))
        session.execute(
            delete(Generation).where(
                Generation.id == identifier, Generation.deleting_at.is_not(None), Generation.status != "RUNNING"
            )
        )
    return True
