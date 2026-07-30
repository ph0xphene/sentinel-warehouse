"""Create metadata schemas and ingestion batch tracking.

Revision ID: 20260730_0001
Revises:
Create Date: 2026-07-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260730_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMAS = ("metadata", "raw", "core", "analytics", "security")


def upgrade() -> None:
    for schema in SCHEMAS:
        op.execute(sa.schema.CreateSchema(schema, if_not_exists=True))

    ingestion_status = postgresql.ENUM(
        "running", "succeeded", "failed", name="ingestion_status", schema="metadata"
    )
    ingestion_status.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "ingestion_batches",
        sa.Column("batch_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_name", sa.String(length=255), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "status",
            postgresql.ENUM(
                "running",
                "succeeded",
                "failed",
                name="ingestion_status",
                schema="metadata",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column("rows_loaded", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("checksum", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("batch_id"),
        schema="metadata",
    )
    op.create_index(
        "ix_metadata_ingestion_batches_source_name",
        "ingestion_batches",
        ["source_name"],
        schema="metadata",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_metadata_ingestion_batches_source_name",
        table_name="ingestion_batches",
        schema="metadata",
    )
    op.drop_table("ingestion_batches", schema="metadata")
    postgresql.ENUM(name="ingestion_status", schema="metadata").drop(op.get_bind(), checkfirst=True)
    for schema in reversed(SCHEMAS):
        op.execute(sa.schema.DropSchema(schema, if_exists=True))
