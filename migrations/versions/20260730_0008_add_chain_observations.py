"""Add canonical Ethereum block and log observations.

Revision ID: 20260730_0008
Revises: 20260730_0007
Create Date: 2026-07-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260730_0008"
down_revision: str | None = "20260730_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("source_checkpoints", sa.Column("chain_id", sa.BigInteger()), schema="metadata")
    op.add_column(
        "source_checkpoints",
        sa.Column("source_identity", sa.String(length=255)),
        schema="metadata",
    )
    op.add_column(
        "source_checkpoints", sa.Column("block_number", sa.BigInteger()), schema="metadata"
    )
    op.add_column(
        "source_checkpoints",
        sa.Column("block_hash", sa.String(length=66)),
        schema="metadata",
    )

    for table in ("financial_records",):
        op.add_column(table, sa.Column("chain_id", sa.BigInteger()), schema="raw")
        op.add_column(table, sa.Column("block_number", sa.BigInteger()), schema="raw")
        op.add_column(table, sa.Column("block_hash", sa.String(length=66)), schema="raw")
        op.add_column(
            table,
            sa.Column("canonical", sa.Boolean(), nullable=False, server_default=sa.true()),
            schema="raw",
        )
        op.alter_column(table, "canonical", server_default=None, schema="raw")

    op.add_column("ethereum_transactions", sa.Column("chain_id", sa.BigInteger()), schema="raw")
    op.add_column(
        "ethereum_transactions", sa.Column("block_hash", sa.String(length=66)), schema="raw"
    )
    op.add_column(
        "ethereum_transactions",
        sa.Column("canonical", sa.Boolean(), nullable=False, server_default=sa.true()),
        schema="raw",
    )
    op.alter_column("ethereum_transactions", "canonical", server_default=None, schema="raw")

    op.add_column("ethereum_transfers", sa.Column("chain_id", sa.BigInteger()), schema="raw")
    op.add_column("ethereum_transfers", sa.Column("block_number", sa.BigInteger()), schema="raw")
    op.add_column("ethereum_transfers", sa.Column("block_hash", sa.String(length=66)), schema="raw")
    op.add_column(
        "ethereum_transfers",
        sa.Column("canonical", sa.Boolean(), nullable=False, server_default=sa.true()),
        schema="raw",
    )
    op.alter_column("ethereum_transfers", "canonical", server_default=None, schema="raw")

    op.create_table(
        "ethereum_blocks",
        sa.Column("observation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("batch_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_name", sa.String(length=255), nullable=False),
        sa.Column("chain_id", sa.BigInteger(), nullable=False),
        sa.Column("block_number", sa.BigInteger(), nullable=False),
        sa.Column("block_hash", sa.String(length=66), nullable=False),
        sa.Column("parent_hash", sa.String(length=66), nullable=False),
        sa.Column("block_timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "observed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("canonical", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(["batch_id"], ["metadata.ingestion_batches.batch_id"]),
        sa.PrimaryKeyConstraint("observation_id"),
        sa.UniqueConstraint(
            "batch_id",
            "chain_id",
            "block_number",
            "block_hash",
            name="uq_ethereum_blocks_batch_identity",
        ),
        schema="raw",
    )
    op.create_index(
        op.f("ix_raw_ethereum_blocks_batch_id"),
        "ethereum_blocks",
        ["batch_id"],
        schema="raw",
    )
    op.create_index(
        op.f("ix_raw_ethereum_blocks_block_number"),
        "ethereum_blocks",
        ["block_number"],
        schema="raw",
    )

    op.create_table(
        "ethereum_logs",
        sa.Column("log_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("batch_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_name", sa.String(length=255), nullable=False),
        sa.Column("chain_id", sa.BigInteger(), nullable=False),
        sa.Column("block_number", sa.BigInteger(), nullable=False),
        sa.Column("block_hash", sa.String(length=66), nullable=False),
        sa.Column("tx_hash", sa.String(length=66), nullable=False),
        sa.Column("log_index", sa.BigInteger(), nullable=False),
        sa.Column("contract_address", sa.String(length=42), nullable=False),
        sa.Column("topics", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("data", sa.Text(), nullable=False),
        sa.Column("removed", sa.Boolean(), nullable=False),
        sa.Column("canonical", sa.Boolean(), nullable=False),
        sa.Column(
            "observed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["batch_id"], ["metadata.ingestion_batches.batch_id"]),
        sa.PrimaryKeyConstraint("log_id"),
        sa.UniqueConstraint(
            "batch_id",
            "tx_hash",
            "log_index",
            name="uq_ethereum_logs_batch_position",
        ),
        schema="raw",
    )
    op.create_index(
        op.f("ix_raw_ethereum_logs_batch_id"),
        "ethereum_logs",
        ["batch_id"],
        schema="raw",
    )
    op.create_index(
        op.f("ix_raw_ethereum_logs_block_number"),
        "ethereum_logs",
        ["block_number"],
        schema="raw",
    )

    op.drop_constraint(
        "uq_financial_events_source_external_id",
        "financial_events",
        schema="core",
        type_="unique",
    )
    op.add_column("financial_events", sa.Column("chain_id", sa.BigInteger()), schema="core")
    op.add_column("financial_events", sa.Column("block_number", sa.BigInteger()), schema="core")
    op.add_column("financial_events", sa.Column("block_hash", sa.String(length=66)), schema="core")
    op.add_column(
        "financial_events",
        sa.Column("canonical", sa.Boolean(), nullable=False, server_default=sa.true()),
        schema="core",
    )
    op.alter_column("financial_events", "canonical", server_default=None, schema="core")
    op.create_index(
        "uq_financial_events_source_external_id",
        "financial_events",
        ["source_system", "external_id"],
        unique=True,
        schema="core",
        postgresql_where=sa.text("canonical"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_financial_events_source_external_id",
        table_name="financial_events",
        schema="core",
    )
    for column in ("canonical", "block_hash", "block_number", "chain_id"):
        op.drop_column("financial_events", column, schema="core")
    op.create_unique_constraint(
        "uq_financial_events_source_external_id",
        "financial_events",
        ["source_system", "external_id"],
        schema="core",
    )

    op.drop_index(
        op.f("ix_raw_ethereum_logs_block_number"),
        table_name="ethereum_logs",
        schema="raw",
    )
    op.drop_index(
        op.f("ix_raw_ethereum_logs_batch_id"),
        table_name="ethereum_logs",
        schema="raw",
    )
    op.drop_table("ethereum_logs", schema="raw")
    op.drop_index(
        op.f("ix_raw_ethereum_blocks_block_number"),
        table_name="ethereum_blocks",
        schema="raw",
    )
    op.drop_index(
        op.f("ix_raw_ethereum_blocks_batch_id"),
        table_name="ethereum_blocks",
        schema="raw",
    )
    op.drop_table("ethereum_blocks", schema="raw")

    for table, columns in (
        ("ethereum_transfers", ("canonical", "block_hash", "block_number", "chain_id")),
        ("ethereum_transactions", ("canonical", "block_hash", "chain_id")),
        ("financial_records", ("canonical", "block_hash", "block_number", "chain_id")),
    ):
        for column in columns:
            op.drop_column(table, column, schema="raw")

    for column in ("block_hash", "block_number", "source_identity", "chain_id"):
        op.drop_column("source_checkpoints", column, schema="metadata")
