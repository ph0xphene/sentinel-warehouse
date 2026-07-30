"""Add reproducible incident cases and attack flows.

Revision ID: 20260730_0009
Revises: 20260730_0008
Create Date: 2026-07-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260730_0009"
down_revision: str | None = "20260730_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "incident_cases",
        sa.Column("case_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("protocol", sa.String(length=100), nullable=False),
        sa.Column("category", sa.String(length=100), nullable=False),
        sa.Column("severity", sa.String(length=20), nullable=False),
        sa.Column(
            "reference_transactions",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column(
            "replay_definition",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("case_id"),
        sa.UniqueConstraint("name", name="uq_incident_cases_name"),
        schema="security",
    )
    op.create_table(
        "attack_flows",
        sa.Column("flow_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("case_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("step_number", sa.Integer(), nullable=False),
        sa.Column("event_id", postgresql.UUID(as_uuid=True)),
        sa.Column("action", sa.String(length=100), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["case_id"], ["security.incident_cases.case_id"]),
        sa.ForeignKeyConstraint(["event_id"], ["core.financial_events.event_id"]),
        sa.PrimaryKeyConstraint("flow_id"),
        sa.UniqueConstraint("case_id", "step_number", name="uq_attack_flows_case_step"),
        schema="security",
    )
    op.create_index(
        op.f("ix_security_attack_flows_case_id"),
        "attack_flows",
        ["case_id"],
        schema="security",
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_security_attack_flows_case_id"),
        table_name="attack_flows",
        schema="security",
    )
    op.drop_table("attack_flows", schema="security")
    op.drop_table("incident_cases", schema="security")
