"""When a schedule version starts applying to new enrolments (doc 9 s8).

Approval and effect are different moments. NULL keeps the previous behaviour -
in force as soon as approved - so every existing row is unchanged.

Revision ID: 20260901_0012
Revises: 20260901_0011
"""
from alembic import op
import sqlalchemy as sa


revision = "20260901_0012"
down_revision = "20260901_0011"
branch_labels = None
depends_on = None

TABLE = "uctsm_schedule_versions"


def _columns() -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(TABLE)}


def upgrade() -> None:
    if TABLE not in set(sa.inspect(op.get_bind()).get_table_names()):
        return
    if "effective_from" not in _columns():
        op.add_column(TABLE, sa.Column("effective_from", sa.Date(), nullable=True))
        op.create_index(f"ix_{TABLE}_effective_from", TABLE, ["effective_from"])


def downgrade() -> None:
    if TABLE not in set(sa.inspect(op.get_bind()).get_table_names()):
        return
    if "effective_from" in _columns():
        op.drop_index(f"ix_{TABLE}_effective_from", table_name=TABLE)
        op.drop_column(TABLE, "effective_from")
