"""Add enterprise pipeline control metadata.

Revision ID: 20260730_0003
Revises: 20260730_0002
Create Date: 2026-07-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260730_0003"
down_revision: str | None = "20260730_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TYPE metadata.ingestion_status ADD VALUE IF NOT EXISTS 'staged'")
    op.execute("ALTER TYPE metadata.ingestion_status ADD VALUE IF NOT EXISTS 'validating'")
    op.execute("ALTER TYPE metadata.ingestion_status ADD VALUE IF NOT EXISTS 'loading'")

    op.add_column(
        "ingestion_batches",
        sa.Column("attempt_count", sa.Integer(), server_default="1", nullable=False),
        schema="metadata",
    )
    op.alter_column(
        "ingestion_batches",
        "attempt_count",
        server_default=None,
        schema="metadata",
    )
    op.create_unique_constraint(
        "uq_ingestion_batches_source_checksum",
        "ingestion_batches",
        ["source_name", "checksum"],
        schema="metadata",
    )

    op.add_column(
        "quality_results",
        sa.Column("attempt_number", sa.Integer(), server_default="1", nullable=False),
        schema="metadata",
    )
    op.alter_column(
        "quality_results",
        "attempt_number",
        server_default=None,
        schema="metadata",
    )

    op.create_table(
        "source_checkpoints",
        sa.Column("checkpoint_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_name", sa.String(length=255), nullable=False),
        sa.Column("checkpoint_value", sa.Text(), nullable=False),
        sa.Column("last_batch_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["last_batch_id"], ["metadata.ingestion_batches.batch_id"]),
        sa.PrimaryKeyConstraint("checkpoint_id"),
        sa.UniqueConstraint("source_name"),
        schema="metadata",
    )

    ingestion_status = postgresql.ENUM(
        "running",
        "staged",
        "validating",
        "loading",
        "succeeded",
        "failed",
        name="ingestion_status",
        schema="metadata",
        create_type=False,
    )
    op.create_table(
        "batch_state_history",
        sa.Column("event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("batch_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("from_status", ingestion_status, nullable=True),
        sa.Column("to_status", ingestion_status, nullable=False),
        sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["batch_id"], ["metadata.ingestion_batches.batch_id"]),
        sa.PrimaryKeyConstraint("event_id"),
        schema="metadata",
    )
    op.create_index(
        "ix_metadata_batch_state_history_batch_id",
        "batch_state_history",
        ["batch_id"],
        schema="metadata",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_metadata_batch_state_history_batch_id",
        table_name="batch_state_history",
        schema="metadata",
    )
    op.drop_table("batch_state_history", schema="metadata")
    op.drop_table("source_checkpoints", schema="metadata")
    op.drop_column("quality_results", "attempt_number", schema="metadata")
    op.drop_constraint(
        "uq_ingestion_batches_source_checksum",
        "ingestion_batches",
        schema="metadata",
        type_="unique",
    )
    op.drop_column("ingestion_batches", "attempt_count", schema="metadata")
