"""Let an anchor be derived from a condition occurrence date.

A progression date or discontinuation date is recorded as a condition occurrence,
not as a visit, but downstream events are timed from it ("survival follow-up every
12 weeks after progression"). Without this column the anchor link is lost on the
repository round-trip and dependent events stay WAITING_FOR_ANCHOR forever.

Revision ID: 20260831_0007
Revises: 20260831_0006
"""
from alembic import op
import sqlalchemy as sa


revision = "20260831_0007"
down_revision = "20260831_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    existing = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("uctsm_anchors")}
    if "source_condition_code" not in existing:
        op.add_column("uctsm_anchors", sa.Column("source_condition_code", sa.String(128), nullable=True))


def downgrade() -> None:
    existing = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("uctsm_anchors")}
    if "source_condition_code" in existing:
        op.drop_column("uctsm_anchors", "source_condition_code")
