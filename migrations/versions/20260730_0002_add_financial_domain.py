"""Add synthetic financial domain and quality metadata.

Revision ID: 20260730_0002
Revises: 20260730_0001
Create Date: 2026-07-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260730_0002"
down_revision: str | None = "20260730_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "quality_results",
        sa.Column("result_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("batch_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("check_name", sa.String(length=100), nullable=False),
        sa.Column("passed", sa.Boolean(), nullable=False),
        sa.Column("records_checked", sa.BigInteger(), nullable=False),
        sa.Column("failure_count", sa.BigInteger(), nullable=False),
        sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["batch_id"], ["metadata.ingestion_batches.batch_id"]),
        sa.PrimaryKeyConstraint("result_id"),
        schema="metadata",
    )
    op.create_index(
        "ix_metadata_quality_results_batch_id",
        "quality_results",
        ["batch_id"],
        schema="metadata",
    )

    op.create_table(
        "financial_records",
        sa.Column("record_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("batch_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_name", sa.String(length=255), nullable=False),
        sa.Column("record_type", sa.String(length=50), nullable=False),
        sa.Column("external_id", sa.String(length=255), nullable=True),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "ingested_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["batch_id"], ["metadata.ingestion_batches.batch_id"]),
        sa.PrimaryKeyConstraint("record_id"),
        schema="raw",
    )
    op.create_index(
        "ix_raw_financial_records_batch_id",
        "financial_records",
        ["batch_id"],
        schema="raw",
    )

    op.create_table(
        "accounts",
        sa.Column("account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_name", sa.String(length=255), nullable=False),
        sa.Column("external_id", sa.String(length=255), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("account_type", sa.String(length=50), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("account_id"),
        sa.UniqueConstraint("source_name", "external_id", name="uq_accounts_source_external_id"),
        schema="core",
    )

    op.create_table(
        "assets",
        sa.Column("asset_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_name", sa.String(length=255), nullable=False),
        sa.Column("external_id", sa.String(length=255), nullable=False),
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("asset_type", sa.String(length=50), nullable=False),
        sa.Column("decimals", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint("decimals >= 0", name="ck_assets_non_negative_decimals"),
        sa.PrimaryKeyConstraint("asset_id"),
        sa.UniqueConstraint("source_name", "external_id", name="uq_assets_source_external_id"),
        schema="core",
    )

    op.create_table(
        "transactions",
        sa.Column("transaction_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("batch_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_name", sa.String(length=255), nullable=False),
        sa.Column("external_id", sa.String(length=255), nullable=False),
        sa.Column("from_account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("to_account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("asset_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("amount", sa.Numeric(precision=38, scale=18), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("description", sa.String(length=500), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint("amount >= 0", name="ck_transactions_non_negative_amount"),
        sa.CheckConstraint(
            "from_account_id <> to_account_id", name="ck_transactions_distinct_accounts"
        ),
        sa.ForeignKeyConstraint(["asset_id"], ["core.assets.asset_id"]),
        sa.ForeignKeyConstraint(["batch_id"], ["metadata.ingestion_batches.batch_id"]),
        sa.ForeignKeyConstraint(["from_account_id"], ["core.accounts.account_id"]),
        sa.ForeignKeyConstraint(["to_account_id"], ["core.accounts.account_id"]),
        sa.PrimaryKeyConstraint("transaction_id"),
        sa.UniqueConstraint(
            "source_name", "external_id", name="uq_transactions_source_external_id"
        ),
        schema="core",
    )

    op.create_table(
        "balances",
        sa.Column("balance_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("batch_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_name", sa.String(length=255), nullable=False),
        sa.Column("external_id", sa.String(length=255), nullable=False),
        sa.Column("account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("asset_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("amount", sa.Numeric(precision=38, scale=18), nullable=False),
        sa.Column("as_of", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["account_id"], ["core.accounts.account_id"]),
        sa.ForeignKeyConstraint(["asset_id"], ["core.assets.asset_id"]),
        sa.ForeignKeyConstraint(["batch_id"], ["metadata.ingestion_batches.batch_id"]),
        sa.PrimaryKeyConstraint("balance_id"),
        sa.UniqueConstraint(
            "account_id", "asset_id", "as_of", name="uq_balances_account_asset_as_of"
        ),
        sa.UniqueConstraint("source_name", "external_id", name="uq_balances_source_external_id"),
        schema="core",
    )


def downgrade() -> None:
    op.drop_table("balances", schema="core")
    op.drop_table("transactions", schema="core")
    op.drop_table("assets", schema="core")
    op.drop_table("accounts", schema="core")
    op.drop_index(
        "ix_raw_financial_records_batch_id",
        table_name="financial_records",
        schema="raw",
    )
    op.drop_table("financial_records", schema="raw")
    op.drop_index(
        "ix_metadata_quality_results_batch_id",
        table_name="quality_results",
        schema="metadata",
    )
    op.drop_table("quality_results", schema="metadata")
