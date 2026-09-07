"""Nested protocol groups: "Part A, Cohort 1" (requirement doc 8 s10).

Both columns are nullable and move together. A cohort that names a parent part
can then be offered only inside that part at enrolment, instead of every cohort
in the protocol appearing valid.

Revision ID: 20260901_0011
Revises: 20260901_0010
"""
from alembic import op
import sqlalchemy as sa


revision = "20260901_0011"
down_revision = "20260901_0010"
branch_labels = None
depends_on = None

TABLES = ("uctsm_arms", "uctsm_cohorts", "uctsm_populations")


def _columns(table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    for table in TABLES:
        if table not in tables:
            continue
        existing = _columns(table)
        if "parent_dimension_type" not in existing:
            op.add_column(table, sa.Column(
                "parent_dimension_type", sa.String(length=32), nullable=True))
        if "parent_code" not in existing:
            op.add_column(table, sa.Column(
                "parent_code", sa.String(length=128), nullable=True))


def downgrade() -> None:
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    for table in TABLES:
        if table not in tables:
            continue
        existing = _columns(table)
        for name in ("parent_code", "parent_dimension_type"):
            if name in existing:
                op.drop_column(table, name)
