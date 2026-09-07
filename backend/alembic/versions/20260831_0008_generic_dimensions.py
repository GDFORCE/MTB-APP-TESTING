"""Persist generic patient dimension assignments.

Revision ID: 20260831_0008
Revises: 20260831_0007
"""
from alembic import op
import sqlalchemy as sa


revision = "20260831_0008"
down_revision = "20260831_0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    existing = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("uctsm_patients")}
    if "dimension_values" not in existing:
        op.add_column("uctsm_patients", sa.Column("dimension_values", sa.JSON(), nullable=True))


def downgrade() -> None:
    existing = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("uctsm_patients")}
    if "dimension_values" in existing:
        op.drop_column("uctsm_patients", "dimension_values")
