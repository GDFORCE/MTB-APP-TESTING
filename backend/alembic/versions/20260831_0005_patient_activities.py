"""Add patient activity occurrences and the append-only intra-day actual record.

Revision ID: 20260831_0005
Revises: 20260831_0004
"""
from alembic import op
import sqlalchemy as sa


revision = "20260831_0005"
down_revision = "20260831_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "uctsm_patient_activities" not in tables:
        op.create_table(
            "uctsm_patient_activities",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("patient_event_id", sa.Uuid(), sa.ForeignKey("uctsm_patient_events.id", ondelete="CASCADE"), nullable=False),
            sa.Column("activity_definition_id", sa.Uuid(), sa.ForeignKey("uctsm_activities.id"), nullable=False),
            sa.Column("activity_code", sa.String(128), nullable=True),
            sa.Column("logical_key", sa.String(320), nullable=False),
            sa.Column("status", sa.String(32), nullable=False),
            sa.Column("requiredness", sa.String(32), nullable=False),
            sa.Column("sequence_number", sa.Integer(), nullable=True),
            sa.Column("planned_time", sa.DateTime(timezone=True), nullable=True),
            sa.Column("earliest_time", sa.DateTime(timezone=True), nullable=True),
            sa.Column("latest_time", sa.DateTime(timezone=True), nullable=True),
            sa.Column("actual_time", sa.DateTime(timezone=True), nullable=True),
            sa.Column("timing_resolution", sa.JSON(), nullable=False),
            sa.Column("generation_reason", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.UniqueConstraint("patient_event_id", "activity_definition_id"),
        )
        op.create_index("ix_uctsm_patient_activities_patient_event_id", "uctsm_patient_activities", ["patient_event_id"])
        op.create_index("ix_uctsm_patient_activities_activity_code", "uctsm_patient_activities", ["activity_code"])
        op.create_index("ix_uctsm_patient_activities_logical_key", "uctsm_patient_activities", ["logical_key"])
        op.create_index("ix_uctsm_patient_activities_status", "uctsm_patient_activities", ["status"])

    if "uctsm_patient_activity_records" not in tables:
        op.create_table(
            "uctsm_patient_activity_records",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("patient_id", sa.Uuid(), sa.ForeignKey("uctsm_patients.id", ondelete="CASCADE"), nullable=False),
            sa.Column("event_code", sa.String(128), nullable=False),
            sa.Column("occurrence_index", sa.Integer(), nullable=False),
            sa.Column("activity_code", sa.String(128), nullable=False),
            sa.Column("logical_key", sa.String(320), nullable=False),
            sa.Column("status", sa.String(32), nullable=False),
            sa.Column("actual_time", sa.DateTime(timezone=True), nullable=True),
            sa.Column("superseded_by_id", sa.Uuid(), nullable=True),
            sa.Column("recorded_by", sa.Uuid(), nullable=True),
            sa.Column("recorded_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("reason", sa.Text(), nullable=True),
        )
        op.create_index("ix_uctsm_patient_activity_records_patient_id", "uctsm_patient_activity_records", ["patient_id"])
        op.create_index("ix_uctsm_patient_activity_records_logical_key", "uctsm_patient_activity_records", ["logical_key"])
        op.create_index("ix_uctsm_patient_activity_records_event_code", "uctsm_patient_activity_records", ["event_code"])
        op.create_index("ix_uctsm_patient_activity_records_activity_code", "uctsm_patient_activity_records", ["activity_code"])


def downgrade() -> None:
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "uctsm_patient_activity_records" in tables:
        op.drop_table("uctsm_patient_activity_records")
    if "uctsm_patient_activities" in tables:
        op.drop_table("uctsm_patient_activities")
