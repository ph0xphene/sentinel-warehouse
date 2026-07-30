import os

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

DATABASE_URL = os.getenv("SENTINEL_TEST_DATABASE_URL")


@pytest.fixture(scope="session")
def migrated_engine():
    if not DATABASE_URL:
        pytest.skip("Set SENTINEL_TEST_DATABASE_URL to run PostgreSQL integration tests")

    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", DATABASE_URL)
    command.upgrade(config, "head")
    engine = create_engine(DATABASE_URL)
    yield engine
    engine.dispose()


@pytest.fixture
def clean_engine(migrated_engine):
    def truncate_domain_tables() -> None:
        with migrated_engine.begin() as connection:
            connection.execute(
                text(
                    """
                    TRUNCATE TABLE
                        security.incident_features,
                        security.attack_flows,
                        security.incident_cases,
                        security.attack_patterns,
                        security.attack_subcategories,
                        security.attack_categories,
                        core.financial_events,
                        core.balances,
                        core.transactions,
                        core.accounts,
                        core.assets,
                        security.incident_evidence,
                        security.incidents,
                        security.invariants,
                        raw.ethereum_logs,
                        raw.ethereum_blocks,
                        raw.ethereum_transfers,
                        raw.ethereum_transactions,
                        raw.financial_records,
                        metadata.quality_results,
                        metadata.batch_state_history,
                        metadata.source_checkpoints,
                        metadata.ingestion_batches
                    """
                )
            )

    truncate_domain_tables()
    yield migrated_engine
    truncate_domain_tables()
