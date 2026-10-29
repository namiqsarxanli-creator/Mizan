"""shadow run errors

Revision ID: d7a31b5c9e42
Revises: c4e9a2f6b813
Create Date: 2026-10-29 18:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d7a31b5c9e42"
down_revision: str | None = "c4e9a2f6b813"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("shadow_runs", sa.Column("error", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("shadow_runs", "error")
