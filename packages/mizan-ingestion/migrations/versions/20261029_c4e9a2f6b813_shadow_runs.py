"""shadow runs

Revision ID: c4e9a2f6b813
Revises: 8b2c4d1e7f30
Create Date: 2026-10-29 17:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c4e9a2f6b813"
down_revision: str | None = "8b2c4d1e7f30"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "shadow_runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("answer_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("candidate", sa.String(length=128), nullable=False),
        sa.Column("answer", sa.Text(), nullable=False),
        sa.Column("refused", sa.Boolean(), nullable=False),
        sa.Column("citations", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("tokens", sa.Integer(), nullable=False),
        sa.Column("latency_ms", sa.Float(), nullable=False),
        sa.Column("primary_tokens", sa.Integer(), nullable=False),
        sa.Column("primary_latency_ms", sa.Float(), nullable=False),
        sa.Column("citation_overlap", sa.Float(), nullable=False),
        sa.Column("refusal_agrees", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(
            ["answer_id"],
            ["answers.id"],
            name=op.f("fk_shadow_runs_answer_id_answers"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_shadow_runs")),
    )
    op.create_index(op.f("ix_shadow_runs_answer_id"), "shadow_runs", ["answer_id"], unique=False)
    op.create_index(
        "ix_shadow_runs_candidate_created_at",
        "shadow_runs",
        ["candidate", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_shadow_runs_candidate_created_at", table_name="shadow_runs")
    op.drop_index(op.f("ix_shadow_runs_answer_id"), table_name="shadow_runs")
    op.drop_table("shadow_runs")
