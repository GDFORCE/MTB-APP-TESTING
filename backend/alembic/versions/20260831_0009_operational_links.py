"""Add stable links to the operational Mongo identities.

Revision ID: 20260831_0009
Revises: 20260831_0008
"""
from alembic import op
import sqlalchemy as sa


revision = "20260831_0009"
down_revision = "20260831_0008"
branch_labels = None
depends_on = None


def _add(table: str, name: str) -> None:
    inspector = sa.inspect(op.get_bind())
    existing = {column["name"] for column in inspector.get_columns(table)}
    if name not in existing:
        op.add_column(table, sa.Column(name, sa.String(length=128), nullable=True))
        op.create_index(f"ix_{table}_{name}", table, [name])


def upgrade() -> None:
    _add("uctsm_trials", "external_trial_id")
    _add("uctsm_schedule_definitions", "external_schedule_definition_id")
    _add("uctsm_patients", "external_patient_id")
    if op.get_bind().dialect.name == "postgresql":
        op.create_unique_constraint(
            "uq_uctsm_trial_external", "uctsm_trials",
            ["organization_id", "external_trial_id"],
        )
        op.create_unique_constraint(
            "uq_uctsm_patient_external", "uctsm_patients",
            ["trial_id", "external_patient_id"],
        )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.drop_constraint("uq_uctsm_patient_external", "uctsm_patients", type_="unique")
        op.drop_constraint("uq_uctsm_trial_external", "uctsm_trials", type_="unique")
    for table, name in (
        ("uctsm_patients", "external_patient_id"),
        ("uctsm_schedule_definitions", "external_schedule_definition_id"),
        ("uctsm_trials", "external_trial_id"),
    ):
        existing = {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}
        if name in existing:
            op.drop_index(f"ix_{table}_{name}", table_name=table)
            op.drop_column(table, name)
