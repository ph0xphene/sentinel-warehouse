from pathlib import Path

import pytest
from sqlalchemy import func, select

from sentinel.ingestion import ingest_ethereum_fixture
from sentinel.models import (
    Account,
    FinancialEvent,
    Incident,
    IngestionStatus,
    RawEthereumTransaction,
    RawEthereumTransfer,
    SourceCheckpoint,
)

pytestmark = pytest.mark.integration

FIXTURES = Path("data/fixtures")


def test_ethereum_events_normalize_correctly_without_incidents(clean_engine) -> None:
    summary = ingest_ethereum_fixture(
        FIXTURES / "ethereum_valid_transfers.json",
        clean_engine,
    )

    with clean_engine.connect() as connection:
        transactions = connection.scalar(select(func.count()).select_from(RawEthereumTransaction))
        transfers = connection.scalar(select(func.count()).select_from(RawEthereumTransfer))
        normalized = connection.execute(
            select(
                FinancialEvent.source_system,
                FinancialEvent.event_type,
                FinancialEvent.amount,
                FinancialEvent.event_metadata.label("event_metadata"),
            )
            .where(FinancialEvent.event_type == "TRANSFER")
            .order_by(FinancialEvent.occurred_at)
        ).all()
        incidents = connection.scalar(select(func.count()).select_from(Incident))

    assert summary.status is IngestionStatus.SUCCEEDED
    assert summary.raw_records == 4
    assert transactions == 2
    assert transfers == 2
    assert [event.source_system for event in normalized] == ["ethereum", "ethereum"]
    assert [event.event_type for event in normalized] == ["TRANSFER", "TRANSFER"]
    assert [event.amount for event in normalized] == [100, 25]
    assert normalized[0].event_metadata["log_index"] == 0
    assert incidents == 0


def test_replayed_ethereum_event_is_detected_before_core_load(clean_engine) -> None:
    first = ingest_ethereum_fixture(
        FIXTURES / "ethereum_valid_transfers.json",
        clean_engine,
    )
    replay = ingest_ethereum_fixture(
        FIXTURES / "ethereum_duplicate_replay.json",
        clean_engine,
    )
    quality = {result.check_name: result for result in replay.quality_results}

    with clean_engine.connect() as connection:
        event_count = connection.scalar(select(func.count()).select_from(FinancialEvent))
        raw_transfer_count = connection.scalar(
            select(func.count()).select_from(RawEthereumTransfer)
        )
        checkpoint = connection.scalar(
            select(SourceCheckpoint.checkpoint_value).where(
                SourceCheckpoint.source_name == "ethereum"
            )
        )
        incident_count = connection.scalar(select(func.count()).select_from(Incident))

    assert first.status is IngestionStatus.SUCCEEDED
    assert replay.status is IngestionStatus.FAILED
    assert not quality["duplicate_external_ids"].passed
    assert event_count == 5
    assert raw_transfer_count == 3
    assert checkpoint == "19000000"
    assert incident_count == 0


def test_impossible_ethereum_balance_creates_incident(clean_engine) -> None:
    summary = ingest_ethereum_fixture(
        FIXTURES / "ethereum_impossible_balance.json",
        clean_engine,
    )
    invariants = {result.name: result for result in summary.invariant_results}

    with clean_engine.connect() as connection:
        incident_type = connection.scalar(
            select(Incident.incident_type).where(Incident.batch_id == summary.batch_id)
        )
        event_count = connection.scalar(select(func.count()).select_from(FinancialEvent))
        account_count = connection.scalar(select(func.count()).select_from(Account))

    assert summary.status is IngestionStatus.FAILED
    assert not invariants["no_negative_balances"].passed
    assert incident_type == "no_negative_balances"
    assert event_count == 0
    assert account_count == 0


def test_successful_ethereum_ingestion_is_idempotent(clean_engine) -> None:
    first = ingest_ethereum_fixture(
        FIXTURES / "ethereum_valid_transfers.json",
        clean_engine,
    )
    duplicate = ingest_ethereum_fixture(
        FIXTURES / "ethereum_valid_transfers.json",
        clean_engine,
    )

    with clean_engine.connect() as connection:
        raw_transaction_count = connection.scalar(
            select(func.count()).select_from(RawEthereumTransaction)
        )
        event_count = connection.scalar(select(func.count()).select_from(FinancialEvent))

    assert duplicate.status is IngestionStatus.SUCCEEDED
    assert duplicate.idempotent is True
    assert duplicate.batch_id == first.batch_id
    assert raw_transaction_count == 2
    assert event_count == 5
