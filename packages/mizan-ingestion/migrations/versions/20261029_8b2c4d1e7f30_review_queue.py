"""review queue

Revision ID: 8b2c4d1e7f30
Revises: 5e111ec8d585
Create Date: 2026-10-29 16:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "8b2c4d1e7f30"
down_revision: str | None = "5e111ec8d585"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "reviews",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("answer_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("reason", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("reviewer", sa.String(length=64), nullable=True),
        sa.Column("corrected_answer", sa.Text(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["answer_id"],
            ["answers.id"],
            name=op.f("fk_reviews_answer_id_answers"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_reviews")),
        sa.UniqueConstraint("answer_id", name=op.f("uq_reviews_answer_id")),
    )
    op.create_index(
        "ix_reviews_status_created_at", "reviews", ["status", "created_at"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_reviews_status_created_at", table_name="reviews")
    op.drop_table("reviews")
