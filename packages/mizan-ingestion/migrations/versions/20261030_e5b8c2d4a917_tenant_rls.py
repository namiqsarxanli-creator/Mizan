"""tenant row-level security on answers

Revision ID: e5b8c2d4a917
Revises: d7a31b5c9e42
Create Date: 2026-10-30 18:00:00.000000

The API works on the quality tables as ``mizan_app``, a role without superuser or BYPASSRLS
(superusers ignore row-level security, even FORCEd). Each transaction states its tenant in
``mizan.tenant``; ``answers`` rows of other tenants are then invisible and cannot be written.
Unset means no rows: forgetting the tenant fails closed. ``'*'`` is the reviewer/analytics
scope. Feedback, online evals, reviews and shadow runs reach answers only through their
foreign key, so they inherit the isolation wherever they are joined to ``answers``.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "e5b8c2d4a917"
down_revision: str | None = "d7a31b5c9e42"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = ("answers", "feedback", "online_evals", "reviews", "shadow_runs")
TENANT = "current_setting('mizan.tenant', true)"


def upgrade() -> None:
    op.execute(
        """
        DO $$ BEGIN
            IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'mizan_app') THEN
                CREATE ROLE mizan_app NOLOGIN NOSUPERUSER NOBYPASSRLS;
            END IF;
        END $$
        """
    )
    op.execute("GRANT mizan_app TO CURRENT_USER")  # so the API's login role may SET ROLE to it
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {', '.join(TABLES)} TO mizan_app")
    op.execute("GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO mizan_app")
    op.execute("ALTER TABLE answers ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE answers FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""
        CREATE POLICY tenant_isolation ON answers
            USING ({TENANT} IN (tenant, '*'))
            WITH CHECK ({TENANT} IN (tenant, '*'))
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON answers")
    op.execute("ALTER TABLE answers NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE answers DISABLE ROW LEVEL SECURITY")
    op.execute(f"REVOKE ALL ON {', '.join(TABLES)} FROM mizan_app")
    op.execute("REVOKE USAGE ON ALL SEQUENCES IN SCHEMA public FROM mizan_app")
