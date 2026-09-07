"""The gate that has to pass before visit reads move to the engine.

``schedule_read_mode`` defaults to LEGACY, so every existing trial keeps reading
from the operational store until someone deliberately moves it.

Revision ID: 20260901_0013
Revises: 20260901_0012
"""
from alembic import op
import sqlalchemy as sa


revision = "20260901_0013"
down_revision = "20260901_0012"
branch_labels = None
depends_on = None


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _columns(table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    tables = _tables()
    if "uctsm_trials" in tables and "schedule_read_mode" not in _columns("uctsm_trials"):
        op.add_column("uctsm_trials", sa.Column(
            "schedule_read_mode", sa.String(length=16), nullable=False,
            server_default="LEGACY"))
    if "uctsm_parity_runs" not in tables:
        op.create_table(
            "uctsm_parity_runs",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("organization_id", sa.Uuid(), nullable=False, index=True),
            sa.Column("trial_id", sa.Uuid(), sa.ForeignKey("uctsm_trials.id", ondelete="CASCADE"), nullable=False, index=True),
            sa.Column("patient_id", sa.Uuid(), sa.ForeignKey("uctsm_patients.id", ondelete="CASCADE"), nullable=True, index=True),
            sa.Column("schedule_version_id", sa.Uuid(), sa.ForeignKey("uctsm_schedule_versions.id"), nullable=True),
            sa.Column("verdict", sa.String(length=32), nullable=False, index=True),
            sa.Column("compared", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("matched", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("differences", sa.JSON(), nullable=True),
            sa.Column("note", sa.Text(), nullable=True),
            sa.Column("ran_by", sa.Uuid(), nullable=True),
            sa.Column("ran_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        )


def downgrade() -> None:
    tables = _tables()
    if "uctsm_parity_runs" in tables:
        op.drop_table("uctsm_parity_runs")
    if "uctsm_trials" in tables and "schedule_read_mode" in _columns("uctsm_trials"):
        op.drop_column("uctsm_trials", "schedule_read_mode")
