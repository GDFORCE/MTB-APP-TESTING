"""Visit mode vocabulary and protocol-defined unscheduled visits.

Requirement doc 10 sections 2-3, 13-15 and 31-33.

``visit_mode`` and ``allowed_visit_modes`` stay nullable and empty by default: an
unstated mode must remain unstated rather than defaulting to a clinic visit, so
no patient is told to travel on a guess.

``activation`` defaults to SCHEDULED, which is what every existing row is.

Revision ID: 20260901_0010
Revises: 20260831_0009
"""
from alembic import op
import sqlalchemy as sa


revision = "20260901_0010"
down_revision = "20260831_0009"
branch_labels = None
depends_on = None


def _columns(table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    tables = _tables()
    if "uctsm_events" in tables:
        existing = _columns("uctsm_events")
        if "visit_mode" not in existing:
            op.add_column("uctsm_events", sa.Column("visit_mode", sa.String(length=64), nullable=True))
        if "allowed_visit_modes" not in existing:
            op.add_column("uctsm_events", sa.Column(
                "allowed_visit_modes", sa.JSON(), nullable=True))
        if "activation" not in existing:
            op.add_column("uctsm_events", sa.Column(
                "activation", sa.String(length=16), nullable=False,
                server_default="SCHEDULED"))

    if "uctsm_patient_events" in tables:
        existing = _columns("uctsm_patient_events")
        if "visit_mode" not in existing:
            op.add_column("uctsm_patient_events", sa.Column(
                "visit_mode", sa.String(length=64), nullable=True))
        if "unscheduled_reason" not in existing:
            op.add_column("uctsm_patient_events", sa.Column(
                "unscheduled_reason", sa.Text(), nullable=True))

    if "uctsm_patient_unscheduled_visits" not in tables:
        op.create_table(
            "uctsm_patient_unscheduled_visits",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("patient_id", sa.Uuid(), sa.ForeignKey("uctsm_patients.id", ondelete="CASCADE"), nullable=False, index=True),
            sa.Column("event_definition_id", sa.Uuid(), sa.ForeignKey("uctsm_events.id"), nullable=False),
            sa.Column("event_code", sa.String(length=128), nullable=False),
            sa.Column("occurred_on", sa.Date(), nullable=False),
            # A reason is mandatory: an unscheduled visit with no stated cause
            # cannot be reviewed later (doc 10 s14).
            sa.Column("reason", sa.Text(), nullable=False),
            sa.Column("visit_mode", sa.String(length=64), nullable=True),
            sa.Column("created_by", sa.Uuid(), nullable=True),
            sa.Column("recorded_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        )


def downgrade() -> None:
    tables = _tables()
    if "uctsm_patient_unscheduled_visits" in tables:
        op.drop_table("uctsm_patient_unscheduled_visits")
    if "uctsm_patient_events" in tables:
        existing = _columns("uctsm_patient_events")
        for name in ("unscheduled_reason", "visit_mode"):
            if name in existing:
                op.drop_column("uctsm_patient_events", name)
    if "uctsm_events" in tables:
        existing = _columns("uctsm_events")
        for name in ("activation", "allowed_visit_modes", "visit_mode"):
            if name in existing:
                op.drop_column("uctsm_events", name)
