"""Add the append-only patient condition lifecycle table.

Revision ID: 20260831_0006
Revises: 20260831_0005
"""
from alembic import op
import sqlalchemy as sa


revision = "20260831_0006"
down_revision = "20260831_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "uctsm_patient_conditions" in set(sa.inspect(op.get_bind()).get_table_names()):
        return
    op.create_table(
        "uctsm_patient_conditions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("patient_id", sa.Uuid(), sa.ForeignKey("uctsm_patients.id", ondelete="CASCADE"), nullable=False),
        sa.Column("condition_code", sa.String(128), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("occurrence_index", sa.Integer(), nullable=False),
        sa.Column("occurrence_date", sa.Date(), nullable=True),
        sa.Column("resolution_date", sa.Date(), nullable=True),
        sa.Column("superseded_by_id", sa.Uuid(), nullable=True),
        sa.Column("impact_proposal_id", sa.Uuid(), nullable=True),
        sa.Column("recorded_by", sa.Uuid(), nullable=True),
        sa.Column("recorded_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
    )
    op.create_index("ix_uctsm_patient_conditions_patient_id", "uctsm_patient_conditions", ["patient_id"])
    op.create_index("ix_uctsm_patient_conditions_condition_code", "uctsm_patient_conditions", ["condition_code"])
    op.create_index("ix_uctsm_patient_conditions_state", "uctsm_patient_conditions", ["state"])


def downgrade() -> None:
    if "uctsm_patient_conditions" in set(sa.inspect(op.get_bind()).get_table_names()):
        op.drop_table("uctsm_patient_conditions")
