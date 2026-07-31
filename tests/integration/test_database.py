import pytest
from sqlalchemy import inspect, text

pytestmark = pytest.mark.integration


def test_ingestion_batches_table_exists(migrated_engine) -> None:
    inspector = inspect(migrated_engine)

    assert "ingestion_batches" in inspector.get_table_names(schema="metadata")
    assert "quality_results" in inspector.get_table_names(schema="metadata")
    assert "source_checkpoints" in inspector.get_table_names(schema="metadata")
    assert "batch_state_history" in inspector.get_table_names(schema="metadata")
    assert "financial_records" in inspector.get_table_names(schema="raw")
    assert {"ethereum_transactions", "ethereum_transfers"} <= set(
        inspector.get_table_names(schema="raw")
    )


def test_core_financial_tables_exist(migrated_engine) -> None:
    inspector = inspect(migrated_engine)

    assert {"accounts", "assets", "transactions", "balances", "financial_events"} <= set(
        inspector.get_table_names(schema="core")
    )
    assert {"invariants", "incidents", "incident_evidence"} <= set(
        inspector.get_table_names(schema="security")
    )


def test_all_platform_schemas_exist(migrated_engine) -> None:
    with migrated_engine.connect() as connection:
        query = text("SELECT schema_name FROM information_schema.schemata")
        schemas = set(connection.execute(query))

    assert {"metadata", "raw", "core", "analytics", "security"} <= {row[0] for row in schemas}


def test_analytics_views_and_event_activity_index_exist(migrated_engine) -> None:
    inspector = inspect(migrated_engine)
    views = set(inspector.get_view_names(schema="analytics"))
    indexes = {
        index["name"]
        for index in inspector.get_indexes(
            "financial_events",
            schema="core",
        )
    }

    assert {"canonical_event_flows", "daily_asset_activity"} <= views
    assert "ix_core_financial_events_activity" in indexes
