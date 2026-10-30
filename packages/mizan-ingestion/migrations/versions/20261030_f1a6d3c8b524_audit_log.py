"""audit log

Revision ID: f1a6d3c8b524
Revises: e5b8c2d4a917
Create Date: 2026-10-30 21:00:00.000000

Append-only for the API: ``mizan_app`` may insert and read, never update or delete.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f1a6d3c8b524"
down_revision: str | None = "e5b8c2d4a917"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "audit_log",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column(
            "at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("subject", sa.String(length=128), nullable=True),
        sa.Column("tenant", sa.String(length=64), nullable=True),
        sa.Column("method", sa.String(length=8), nullable=False),
        sa.Column("path", sa.String(length=256), nullable=False),
        sa.Column("status", sa.SmallInteger(), nullable=False),
        sa.Column("outcome", sa.String(length=16), nullable=False),
        sa.Column("trace_id", sa.String(length=32), nullable=True),
        sa.Column("latency_ms", sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_log")),
    )
    op.create_index("ix_audit_log_tenant_at", "audit_log", ["tenant", "at"], unique=False)
    op.execute("GRANT SELECT, INSERT ON audit_log TO mizan_app")
    op.execute("GRANT USAGE ON SEQUENCE audit_log_id_seq TO mizan_app")


def downgrade() -> None:
    op.execute("REVOKE ALL ON audit_log FROM mizan_app")
    op.drop_index("ix_audit_log_tenant_at", table_name="audit_log")
    op.drop_table("audit_log")
