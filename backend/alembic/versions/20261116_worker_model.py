"""Publish which model the worker's inference process holds (P5.1)."""

import sqlalchemy as sa

from alembic import op

revision = "20261116_worker_model"
down_revision = "20261105_ingest_reservations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("worker", sa.Column("inference_model_id", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("worker", "inference_model_id")
