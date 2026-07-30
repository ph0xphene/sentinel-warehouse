"""Add source-native Ethereum raw tables.

Revision ID: 20260730_0006
Revises: 20260730_0005
Create Date: 2026-07-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260730_0006"
down_revision: str | None = "20260730_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ethereum_transactions",
        sa.Column("batch_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tx_hash", sa.String(length=66), nullable=False),
        sa.Column("block_number", sa.BigInteger(), nullable=False),
        sa.Column("block_timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("from_address", sa.String(length=42), nullable=False),
        sa.Column("to_address", sa.String(length=42), nullable=True),
        sa.Column("success", sa.Boolean(), nullable=False),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.ForeignKeyConstraint(["batch_id"], ["metadata.ingestion_batches.batch_id"]),
        sa.PrimaryKeyConstraint("batch_id", "tx_hash"),
        schema="raw",
    )
    op.create_index(
        "ix_raw_ethereum_transactions_block_number",
        "ethereum_transactions",
        ["block_number"],
        schema="raw",
    )

    op.create_table(
        "ethereum_transfers",
        sa.Column("batch_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tx_hash", sa.String(length=66), nullable=False),
        sa.Column("log_index", sa.BigInteger(), nullable=False),
        sa.Column("token_address", sa.String(length=42), nullable=False),
        sa.Column("from_address", sa.String(length=42), nullable=False),
        sa.Column("to_address", sa.String(length=42), nullable=False),
        sa.Column("amount", sa.Numeric(precision=78, scale=0), nullable=False),
        sa.ForeignKeyConstraint(["batch_id"], ["metadata.ingestion_batches.batch_id"]),
        sa.PrimaryKeyConstraint("batch_id", "tx_hash", "log_index"),
        schema="raw",
    )


def downgrade() -> None:
    op.drop_table("ethereum_transfers", schema="raw")
    op.drop_index(
        "ix_raw_ethereum_transactions_block_number",
        table_name="ethereum_transactions",
        schema="raw",
    )
    op.drop_table("ethereum_transactions", schema="raw")
