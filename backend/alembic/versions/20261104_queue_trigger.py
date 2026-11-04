"""Persist the next attempt trigger instead of guessing from prior outcomes."""

import sqlalchemy as sa

from alembic import op

revision = "20261104_queue_trigger"
down_revision = "511f1d25ba38"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Inline CHECK allows ALTER ADD COLUMN without rebuilding the parent table:
    # rebuilding it with foreign_keys=ON could cascade-delete attempt/artifact rows.
    op.add_column(
        "generation",
        sa.Column(
            "next_attempt_trigger",
            sa.String(16),
            sa.CheckConstraint(
                "next_attempt_trigger IN ('INITIAL', 'AUTO_RETRY', 'MANUAL_RETRY', 'RECOVERY', 'RESUME')",
                name="ck_generation_next_attempt_trigger",
            ),
            server_default="INITIAL",
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("generation", "next_attempt_trigger")
