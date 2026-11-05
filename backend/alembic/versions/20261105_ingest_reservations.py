"""Persist cross-process upload admission leases without rebuilding parent tables."""

import sqlalchemy as sa

from alembic import op

revision = "20261105_ingest_reservations"
down_revision = "20261104_queue_trigger"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("system_state", sa.Column("ingest_reservations", sa.JSON(), nullable=False, server_default="{}"))


def downgrade() -> None:
    op.drop_column("system_state", "ingest_reservations")
