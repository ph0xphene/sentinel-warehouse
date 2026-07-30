"""Harden invariant context, finding provenance, ordering, and analysis status.

Revision ID: 20260730_0012
Revises: 20260730_0011
Create Date: 2026-07-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260730_0012"
down_revision: str | None = "20260730_0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

analysis_status = postgresql.ENUM(
    "SUPPORTED",
    "PARTIALLY_SUPPORTED",
    "UNSUPPORTED",
    name="analysis_status",
    schema="metadata",
)
incident_origin = postgresql.ENUM(
    "LIVE",
    "REPLAY",
    "FIXTURE",
    name="incident_origin",
    schema="security",
)


def upgrade() -> None:
    op.alter_column(
        "invariants",
        "execution_result",
        schema="security",
        existing_type=sa.String(length=20),
        type_=sa.String(length=32),
        existing_nullable=False,
    )
    bind = op.get_bind()
    analysis_status.create(bind, checkfirst=True)
    incident_origin.create(bind, checkfirst=True)
    analysis_status_type = postgresql.ENUM(
        "SUPPORTED",
        "PARTIALLY_SUPPORTED",
        "UNSUPPORTED",
        name="analysis_status",
        schema="metadata",
        create_type=False,
    )
    incident_origin_type = postgresql.ENUM(
        "LIVE",
        "REPLAY",
        "FIXTURE",
        name="incident_origin",
        schema="security",
        create_type=False,
    )

    op.add_column(
        "ingestion_batches",
        sa.Column(
            "analysis_status",
            analysis_status_type,
            nullable=False,
            server_default="SUPPORTED",
        ),
        schema="metadata",
    )
    op.add_column(
        "ingestion_batches",
        sa.Column(
            "origin",
            incident_origin_type,
            nullable=False,
            server_default="FIXTURE",
        ),
        schema="metadata",
    )
    op.add_column(
        "ingestion_batches",
        sa.Column("case_id", postgresql.UUID(as_uuid=True)),
        schema="metadata",
    )
    op.create_index(
        op.f("ix_metadata_ingestion_batches_case_id"),
        "ingestion_batches",
        ["case_id"],
        schema="metadata",
    )
    op.create_foreign_key(
        "fk_ingestion_batches_case_id",
        "ingestion_batches",
        "incident_cases",
        ["case_id"],
        ["case_id"],
        source_schema="metadata",
        referent_schema="security",
    )
    op.alter_column(
        "ingestion_batches",
        "analysis_status",
        server_default=None,
        schema="metadata",
    )
    op.alter_column(
        "ingestion_batches",
        "origin",
        server_default=None,
        schema="metadata",
    )

    for table in ("invariants", "incidents"):
        op.add_column(
            table,
            sa.Column(
                "origin",
                incident_origin_type,
                nullable=False,
                server_default="FIXTURE",
            ),
            schema="security",
        )
        op.add_column(
            table,
            sa.Column("case_id", postgresql.UUID(as_uuid=True)),
            schema="security",
        )
        op.create_foreign_key(
            f"fk_{table}_case_id",
            table,
            "incident_cases",
            ["case_id"],
            ["case_id"],
            source_schema="security",
            referent_schema="security",
        )
        op.create_index(
            op.f(f"ix_security_{table}_case_id"),
            table,
            ["case_id"],
            schema="security",
        )
        op.alter_column(table, "origin", server_default=None, schema="security")

    op.drop_constraint(
        "uq_incidents_batch_type",
        "incidents",
        schema="security",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_incidents_batch_type_origin",
        "incidents",
        ["batch_id", "incident_type", "origin"],
        schema="security",
    )

    op.add_column(
        "incident_evidence",
        sa.Column(
            "origin",
            incident_origin_type,
            nullable=False,
            server_default="FIXTURE",
        ),
        schema="security",
    )
    op.alter_column(
        "incident_evidence",
        "origin",
        server_default=None,
        schema="security",
    )

    op.add_column(
        "financial_events",
        sa.Column("transaction_index", sa.BigInteger()),
        schema="core",
    )
    op.add_column(
        "financial_events",
        sa.Column("log_index", sa.BigInteger()),
        schema="core",
    )
    op.add_column(
        "financial_events",
        sa.Column(
            "checker_authorized",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        schema="core",
    )
    op.alter_column(
        "financial_events",
        "checker_authorized",
        server_default=None,
        schema="core",
    )
    op.create_index(
        "ix_core_financial_events_chain_order",
        "financial_events",
        ["chain_id", "block_number", "transaction_index", "log_index"],
        schema="core",
    )

    for table in ("ethereum_transactions", "ethereum_transfers", "ethereum_logs"):
        op.add_column(
            table,
            sa.Column("transaction_index", sa.BigInteger()),
            schema="raw",
        )


def downgrade() -> None:
    for table in ("ethereum_logs", "ethereum_transfers", "ethereum_transactions"):
        op.drop_column(table, "transaction_index", schema="raw")

    op.drop_index(
        "ix_core_financial_events_chain_order",
        table_name="financial_events",
        schema="core",
    )
    op.drop_column("financial_events", "checker_authorized", schema="core")
    op.drop_column("financial_events", "log_index", schema="core")
    op.drop_column("financial_events", "transaction_index", schema="core")

    op.drop_column("incident_evidence", "origin", schema="security")
    op.drop_constraint(
        "uq_incidents_batch_type_origin",
        "incidents",
        schema="security",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_incidents_batch_type",
        "incidents",
        ["batch_id", "incident_type"],
        schema="security",
    )

    for table in ("incidents", "invariants"):
        op.drop_index(
            op.f(f"ix_security_{table}_case_id"),
            table_name=table,
            schema="security",
        )
        op.drop_constraint(
            f"fk_{table}_case_id",
            table,
            schema="security",
            type_="foreignkey",
        )
        op.drop_column(table, "case_id", schema="security")
        op.drop_column(table, "origin", schema="security")

    op.drop_constraint(
        "fk_ingestion_batches_case_id",
        "ingestion_batches",
        schema="metadata",
        type_="foreignkey",
    )
    op.drop_index(
        op.f("ix_metadata_ingestion_batches_case_id"),
        table_name="ingestion_batches",
        schema="metadata",
    )
    op.drop_column("ingestion_batches", "case_id", schema="metadata")
    op.drop_column("ingestion_batches", "origin", schema="metadata")
    op.drop_column("ingestion_batches", "analysis_status", schema="metadata")

    incident_origin.drop(op.get_bind(), checkfirst=True)
    analysis_status.drop(op.get_bind(), checkfirst=True)
    op.alter_column(
        "invariants",
        "execution_result",
        schema="security",
        existing_type=sa.String(length=32),
        type_=sa.String(length=20),
        existing_nullable=False,
    )
