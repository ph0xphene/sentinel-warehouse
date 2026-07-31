"""Prepare read-only analytics views and canonical event access path.

Revision ID: 20260730_0013
Revises: 20260730_0012
Create Date: 2026-07-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260730_0013"
down_revision: str | None = "20260730_0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_core_financial_events_activity",
        "financial_events",
        ["occurred_at", "asset_id"],
        schema="core",
        postgresql_where=sa.text("canonical"),
    )
    op.execute(
        """
        CREATE VIEW analytics.canonical_event_flows AS
        SELECT
            event.event_id,
            event.batch_id,
            event.source_system,
            event.external_id,
            event.chain_id,
            event.block_number,
            event.transaction_index,
            event.log_index,
            event.block_hash,
            event.event_type,
            event.occurred_at,
            asset.external_id AS asset_external_id,
            account_from.external_id AS account_from_external_id,
            account_to.external_id AS account_to_external_id,
            event.amount,
            event.created_at
        FROM core.financial_events AS event
        LEFT JOIN core.assets AS asset
            ON asset.asset_id = event.asset_id
        LEFT JOIN core.accounts AS account_from
            ON account_from.account_id = event.account_from_id
        LEFT JOIN core.accounts AS account_to
            ON account_to.account_id = event.account_to_id
        WHERE event.canonical
        """
    )
    op.execute(
        """
        CREATE VIEW analytics.daily_asset_activity AS
        SELECT
            occurred_at::date AS activity_date,
            source_system,
            asset_external_id,
            event_type,
            count(*) AS event_count,
            sum(abs(amount)) AS gross_amount
        FROM analytics.canonical_event_flows
        GROUP BY
            occurred_at::date,
            source_system,
            asset_external_id,
            event_type
        """
    )


def downgrade() -> None:
    op.execute("DROP VIEW analytics.daily_asset_activity")
    op.execute("DROP VIEW analytics.canonical_event_flows")
    op.drop_index(
        "ix_core_financial_events_activity",
        table_name="financial_events",
        schema="core",
    )
