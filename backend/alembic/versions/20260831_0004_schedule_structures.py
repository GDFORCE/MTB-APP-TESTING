"""Add versioned conditional, repeat, dimension, and confinement structures.

Revision ID: 20260831_0004
Revises: 20260831_0003
"""
from alembic import op
import sqlalchemy as sa


revision = "20260831_0004"
down_revision = "20260831_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    existing = {column["name"] for column in inspector.get_columns("uctsm_schedule_versions")}
    for column in (
        sa.Column("dimensions", sa.JSON(), nullable=True),
        sa.Column("conditional_definitions", sa.JSON(), nullable=True),
        sa.Column("repeat_blocks", sa.JSON(), nullable=True),
        sa.Column("confinement_episodes", sa.JSON(), nullable=True),
    ):
        if column.name not in existing:
            op.add_column("uctsm_schedule_versions", column)


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    existing = {column["name"] for column in inspector.get_columns("uctsm_schedule_versions")}
    for name in ("confinement_episodes", "repeat_blocks", "conditional_definitions", "dimensions"):
        if name in existing:
            op.drop_column("uctsm_schedule_versions", name)
