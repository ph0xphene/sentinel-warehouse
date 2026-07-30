"""Add security incidents and structured evidence.

Revision ID: 20260730_0005
Revises: 20260730_0004
Create Date: 2026-07-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260730_0005"
down_revision: str | None = "20260730_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    incident_status = postgresql.ENUM(
        "OPEN",
        "INVESTIGATING",
        "RESOLVED",
        "IGNORED",
        name="incident_status",
        schema="security",
    )
    incident_status.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "incidents",
        sa.Column("incident_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("incident_type", sa.String(length=100), nullable=False),
        sa.Column("severity", sa.String(length=20), nullable=False),
        sa.Column(
            "status",
            postgresql.ENUM(
                "OPEN",
                "INVESTIGATING",
                "RESOLVED",
                "IGNORED",
                name="incident_status",
                schema="security",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("batch_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["batch_id"], ["metadata.ingestion_batches.batch_id"]),
        sa.PrimaryKeyConstraint("incident_id"),
        sa.UniqueConstraint("batch_id", "incident_type", name="uq_incidents_batch_type"),
        schema="security",
    )
    op.create_index(
        "ix_security_incidents_batch_id",
        "incidents",
        ["batch_id"],
        schema="security",
    )

    op.create_table(
        "incident_evidence",
        sa.Column("evidence_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("incident_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("affected_entity", sa.String(length=255), nullable=False),
        sa.Column("evidence_type", sa.String(length=100), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["incident_id"], ["security.incidents.incident_id"]),
        sa.PrimaryKeyConstraint("evidence_id"),
        schema="security",
    )
    op.create_index(
        "ix_security_incident_evidence_incident_id",
        "incident_evidence",
        ["incident_id"],
        schema="security",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_security_incident_evidence_incident_id",
        table_name="incident_evidence",
        schema="security",
    )
    op.drop_table("incident_evidence", schema="security")
    op.drop_index(
        "ix_security_incidents_batch_id",
        table_name="incidents",
        schema="security",
    )
    op.drop_table("incidents", schema="security")
    postgresql.ENUM(name="incident_status", schema="security").drop(op.get_bind(), checkfirst=True)
