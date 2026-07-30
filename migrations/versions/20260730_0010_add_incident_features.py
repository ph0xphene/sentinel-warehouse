"""Add attack-pattern labels and incident features.

Revision ID: 20260730_0010
Revises: 20260730_0009
Create Date: 2026-07-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260730_0010"
down_revision: str | None = "20260730_0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "attack_patterns",
        sa.Column("pattern_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("category", sa.String(length=100), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("pattern_id"),
        sa.UniqueConstraint("name", name="uq_attack_patterns_name"),
        schema="security",
    )
    op.add_column(
        "incident_cases",
        sa.Column("attack_pattern_id", postgresql.UUID(as_uuid=True)),
        schema="security",
    )
    op.create_foreign_key(
        "fk_incident_cases_attack_pattern_id",
        "incident_cases",
        "attack_patterns",
        ["attack_pattern_id"],
        ["pattern_id"],
        source_schema="security",
        referent_schema="security",
    )
    op.create_index(
        op.f("ix_security_incident_cases_attack_pattern_id"),
        "incident_cases",
        ["attack_pattern_id"],
        schema="security",
    )
    op.create_table(
        "incident_features",
        sa.Column("case_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("feature_name", sa.String(length=100), nullable=False),
        sa.Column("numeric_value", sa.Numeric(precision=38, scale=18)),
        sa.Column("categorical_value", sa.Text()),
        sa.CheckConstraint(
            "(numeric_value IS NOT NULL) <> (categorical_value IS NOT NULL)",
            name="ck_incident_features_exactly_one_value",
        ),
        sa.ForeignKeyConstraint(["case_id"], ["security.incident_cases.case_id"]),
        sa.PrimaryKeyConstraint("case_id", "feature_name"),
        schema="security",
    )


def downgrade() -> None:
    op.drop_table("incident_features", schema="security")
    op.drop_index(
        op.f("ix_security_incident_cases_attack_pattern_id"),
        table_name="incident_cases",
        schema="security",
    )
    op.drop_constraint(
        "fk_incident_cases_attack_pattern_id",
        "incident_cases",
        schema="security",
        type_="foreignkey",
    )
    op.drop_column("incident_cases", "attack_pattern_id", schema="security")
    op.drop_table("attack_patterns", schema="security")
