"""Add incident taxonomy and rich corpus metadata.

Revision ID: 20260730_0011
Revises: 20260730_0010
Create Date: 2026-07-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260730_0011"
down_revision: str | None = "20260730_0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "attack_categories",
        sa.Column("category_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("category_id"),
        sa.UniqueConstraint("name", name="uq_attack_categories_name"),
        schema="security",
    )
    op.create_table(
        "attack_subcategories",
        sa.Column("subcategory_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("category_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["category_id"],
            ["security.attack_categories.category_id"],
        ),
        sa.PrimaryKeyConstraint("subcategory_id"),
        sa.UniqueConstraint(
            "category_id",
            "name",
            name="uq_attack_subcategories_category_name",
        ),
        schema="security",
    )
    op.create_index(
        op.f("ix_security_attack_subcategories_category_id"),
        "attack_subcategories",
        ["category_id"],
        schema="security",
    )
    op.add_column(
        "attack_patterns",
        sa.Column("subcategory_id", postgresql.UUID(as_uuid=True)),
        schema="security",
    )
    op.create_foreign_key(
        "fk_attack_patterns_subcategory_id",
        "attack_patterns",
        "attack_subcategories",
        ["subcategory_id"],
        ["subcategory_id"],
        source_schema="security",
        referent_schema="security",
    )
    op.create_index(
        op.f("ix_security_attack_patterns_subcategory_id"),
        "attack_patterns",
        ["subcategory_id"],
        schema="security",
    )

    op.add_column(
        "incident_cases",
        sa.Column("chain", sa.String(length=100)),
        schema="security",
    )
    op.add_column(
        "incident_cases",
        sa.Column("confidence_level", sa.String(length=20)),
        schema="security",
    )
    op.add_column(
        "incident_cases",
        sa.Column("affected_contracts", postgresql.JSONB(astext_type=sa.Text())),
        schema="security",
    )
    op.add_column(
        "incident_cases",
        sa.Column("attacker_addresses", postgresql.JSONB(astext_type=sa.Text())),
        schema="security",
    )
    op.add_column(
        "incident_cases",
        sa.Column("external_references", postgresql.JSONB(astext_type=sa.Text())),
        schema="security",
    )


def downgrade() -> None:
    for column in (
        "external_references",
        "attacker_addresses",
        "affected_contracts",
        "confidence_level",
        "chain",
    ):
        op.drop_column("incident_cases", column, schema="security")

    op.drop_index(
        op.f("ix_security_attack_patterns_subcategory_id"),
        table_name="attack_patterns",
        schema="security",
    )
    op.drop_constraint(
        "fk_attack_patterns_subcategory_id",
        "attack_patterns",
        schema="security",
        type_="foreignkey",
    )
    op.drop_column("attack_patterns", "subcategory_id", schema="security")
    op.drop_index(
        op.f("ix_security_attack_subcategories_category_id"),
        table_name="attack_subcategories",
        schema="security",
    )
    op.drop_table("attack_subcategories", schema="security")
    op.drop_table("attack_categories", schema="security")
