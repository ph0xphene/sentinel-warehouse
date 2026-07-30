"""Add canonical financial events and invariant results.

Revision ID: 20260730_0004
Revises: 20260730_0003
Create Date: 2026-07-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260730_0004"
down_revision: str | None = "20260730_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TYPE metadata.ingestion_status ADD VALUE IF NOT EXISTS 'invariant_checking'")

    op.create_table(
        "financial_events",
        sa.Column("event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("batch_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_system", sa.String(length=255), nullable=False),
        sa.Column("external_id", sa.String(length=255), nullable=False),
        sa.Column("event_type", sa.String(length=50), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("asset_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("account_from_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("account_to_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("amount", sa.Numeric(precision=38, scale=18), nullable=True),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["account_from_id"], ["core.accounts.account_id"]),
        sa.ForeignKeyConstraint(["account_to_id"], ["core.accounts.account_id"]),
        sa.ForeignKeyConstraint(["asset_id"], ["core.assets.asset_id"]),
        sa.ForeignKeyConstraint(["batch_id"], ["metadata.ingestion_batches.batch_id"]),
        sa.PrimaryKeyConstraint("event_id"),
        sa.UniqueConstraint(
            "source_system",
            "external_id",
            name="uq_financial_events_source_external_id",
        ),
        schema="core",
    )
    op.create_index(
        "ix_core_financial_events_batch_id",
        "financial_events",
        ["batch_id"],
        schema="core",
    )

    op.create_table(
        "invariants",
        sa.Column("invariant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("batch_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("severity", sa.String(length=20), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("execution_result", sa.String(length=20), nullable=False),
        sa.Column("affected_records", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["batch_id"], ["metadata.ingestion_batches.batch_id"]),
        sa.PrimaryKeyConstraint("invariant_id"),
        schema="security",
    )
    op.create_index(
        "ix_security_invariants_batch_id",
        "invariants",
        ["batch_id"],
        schema="security",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_security_invariants_batch_id",
        table_name="invariants",
        schema="security",
    )
    op.drop_table("invariants", schema="security")
    op.drop_index(
        "ix_core_financial_events_batch_id",
        table_name="financial_events",
        schema="core",
    )
    op.drop_table("financial_events", schema="core")
