"""Add patient assignment, impact proposal, and stable event identity.

Revision ID: 20260831_0003
Revises: 20260831_0002
"""
from alembic import op
import sqlalchemy as sa

from app.db.base import Base
from app.db import models  # noqa: F401


revision = "20260831_0003"
down_revision = "20260831_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Keep compatibility with revision 0001's historical create_all behavior.
    Base.metadata.create_all(bind=op.get_bind(), checkfirst=True)
    inspector = sa.inspect(op.get_bind())
    existing = {column["name"] for column in inspector.get_columns("uctsm_patient_events")}
    additions = (
        sa.Column("logical_occurrence_id", sa.Uuid(), nullable=True),
        sa.Column("logical_key", sa.String(length=256), nullable=True),
        sa.Column("supersedes_patient_event_id", sa.Uuid(), nullable=True),
        sa.Column("protected_history", sa.Boolean(), nullable=True),
    )
    for column in additions:
        if column.name not in existing:
            op.add_column("uctsm_patient_events", column)
    # Existing evaluation rows remain historical. Give each a stable identity/key so
    # subsequent reconciliation can link without pretending unrelated rows are equal.
    if op.get_bind().dialect.name == "postgresql":
        op.execute("UPDATE uctsm_patient_events SET logical_occurrence_id = gen_random_uuid() WHERE logical_occurrence_id IS NULL")
        op.execute("""
            UPDATE uctsm_patient_events pe
            SET logical_key = e.code || ':' || pe.occurrence_index::text
            FROM uctsm_events e
            WHERE pe.event_definition_id = e.id AND pe.logical_key IS NULL
        """)
        op.execute("UPDATE uctsm_patient_events SET protected_history = false WHERE protected_history IS NULL")
        op.create_foreign_key(
            "fk_uctsm_patient_event_supersedes", "uctsm_patient_events", "uctsm_patient_events",
            ["supersedes_patient_event_id"], ["id"],
        )
        op.create_index("ix_uctsm_patient_events_logical_occurrence", "uctsm_patient_events", ["logical_occurrence_id"])
        op.create_index("ix_uctsm_patient_events_logical_key", "uctsm_patient_events", ["logical_key"])


def downgrade() -> None:
    # Assignment/proposal tables are included in current metadata and intentionally
    # retained on downgrade to avoid deleting clinical/audit history.
    inspector = sa.inspect(op.get_bind())
    existing = {column["name"] for column in inspector.get_columns("uctsm_patient_events")}
    if op.get_bind().dialect.name == "postgresql":
        for index in ("ix_uctsm_patient_events_logical_key", "ix_uctsm_patient_events_logical_occurrence"):
            op.drop_index(index, table_name="uctsm_patient_events", if_exists=True)
        op.drop_constraint("fk_uctsm_patient_event_supersedes", "uctsm_patient_events", type_="foreignkey")
    for name in ("protected_history", "supersedes_patient_event_id", "logical_key", "logical_occurrence_id"):
        if name in existing:
            op.drop_column("uctsm_patient_events", name)
