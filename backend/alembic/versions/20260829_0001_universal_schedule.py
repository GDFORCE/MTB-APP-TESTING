"""Create the isolated Universal Clinical Trial Schedule schema.

Revision ID: 20260829_0001
Revises: None
"""
from alembic import op

from app.db.base import Base
from app.db import models  # noqa: F401

revision = "20260829_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Metadata is the single declarative schema contract; checkfirst makes this safe
    # for deployments where extensions or tenant setup ran before Alembic.
    Base.metadata.create_all(bind=op.get_bind(), checkfirst=True)
    if op.get_bind().dialect.name == "postgresql":
        op.execute("""
        CREATE OR REPLACE FUNCTION uctsm_reject_approved_schedule_mutation()
        RETURNS trigger AS $$
        BEGIN
          IF EXISTS (
            SELECT 1 FROM uctsm_schedule_versions sv
            WHERE sv.id = COALESCE(OLD.schedule_version_id, NEW.schedule_version_id)
              AND sv.status IN ('APPROVED', 'SUPERSEDED')
          ) THEN
            RAISE EXCEPTION 'approved UCTSM schedule versions are immutable';
          END IF;
          RETURN COALESCE(NEW, OLD);
        END;
        $$ LANGUAGE plpgsql;
        """)
        for table in (
            "uctsm_epochs", "uctsm_arms", "uctsm_cohorts", "uctsm_populations",
            "uctsm_anchors", "uctsm_events", "uctsm_event_dependencies",
            "uctsm_evidence", "uctsm_claim_evidence", "uctsm_validation_issues",
        ):
            op.execute(f"""
            CREATE TRIGGER {table}_immutable
            BEFORE UPDATE OR DELETE ON {table}
            FOR EACH ROW EXECUTE FUNCTION uctsm_reject_approved_schedule_mutation()
            """)


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP FUNCTION IF EXISTS uctsm_reject_approved_schedule_mutation() CASCADE")
    Base.metadata.drop_all(bind=op.get_bind(), checkfirst=True)

