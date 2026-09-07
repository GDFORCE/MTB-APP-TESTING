"""Add explicit UCTSM clinical schedule semantics.

Revision ID: 20260831_0002
Revises: 20260829_0001
"""
from alembic import op
import sqlalchemy as sa


revision = "20260831_0002"
down_revision = "20260829_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Revision 0001 historically calls Base.metadata.create_all(). On a brand-new
    # deployment that can expose current-model columns before this revision runs,
    # so check the physical schema and remain safe for both fresh and upgraded DBs.
    inspector = sa.inspect(op.get_bind())
    existing = {
        table: {column["name"] for column in inspector.get_columns(table)}
        for table in ("uctsm_events", "uctsm_activities")
    }
    additions = {
        "uctsm_events": (
            sa.Column("dependency_mode", sa.String(length=32), nullable=True),
            sa.Column("conditional_actions", sa.JSON(), nullable=True),
            sa.Column("qualifiers", sa.JSON(), nullable=True),
            sa.Column("confinement", sa.JSON(), nullable=True),
        ),
        "uctsm_activities": (
            sa.Column("applicability", sa.JSON(), nullable=True),
            sa.Column("qualifiers", sa.JSON(), nullable=True),
        ),
    }
    for table, columns in additions.items():
        for column in columns:
            if column.name not in existing[table]:
                op.add_column(table, column)


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    for table, names in (
        ("uctsm_activities", ("qualifiers", "applicability")),
        ("uctsm_events", ("confinement", "qualifiers", "conditional_actions", "dependency_mode")),
    ):
        existing = {column["name"] for column in inspector.get_columns(table)}
        for name in names:
            if name in existing:
                op.drop_column(table, name)
