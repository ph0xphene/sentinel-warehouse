import json
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from sentinel.ingestion import ingest_fixture
from sentinel.ingestion.failures import FailureInjector, FailurePoint
from sentinel.models import (
    Account,
    Asset,
    BatchStateHistory,
    FinancialEvent,
    FinancialTransaction,
    IngestionBatch,
    IngestionStatus,
    InvariantResult,
    QualityResult,
    RawFinancialRecord,
    SourceCheckpoint,
)
from sentinel.quality import CheckPolicy, QualityConfig
from sentinel.security import CanonicalEvent, reconstruct_balances

pytestmark = pytest.mark.integration

FIXTURES = Path("data/fixtures")


def test_successful_ingestion_loads_raw_and_core(clean_engine) -> None:
    summary = ingest_fixture(FIXTURES / "synthetic_financial.json", clean_engine)

    assert summary.status is IngestionStatus.SUCCEEDED
    assert summary.raw_records == 7
    assert summary.core_records == 7
    assert all(result.passed for result in summary.quality_results)

    with clean_engine.connect() as connection:
        raw_count = connection.scalar(select(func.count()).select_from(RawFinancialRecord))
        account_count = connection.scalar(select(func.count()).select_from(Account))
        quality_count = connection.scalar(select(func.count()).select_from(QualityResult))

    assert raw_count == 7
    assert account_count == 2
    assert quality_count == 4
    assert all(result.passed for result in summary.invariant_results)


def test_duplicate_detection_fails_batch_without_core_load(clean_engine) -> None:
    summary = ingest_fixture(FIXTURES / "duplicate_external_ids.json", clean_engine)
    outcomes = {result.check_name: result for result in summary.quality_results}

    assert summary.status is IngestionStatus.FAILED
    assert summary.raw_records == 3
    assert summary.core_records == 0
    assert not outcomes["duplicate_external_ids"].passed

    with clean_engine.connect() as connection:
        account_count = connection.scalar(select(func.count()).select_from(Account))
        persisted_passed = connection.scalar(
            select(QualityResult.passed).where(
                QualityResult.batch_id == summary.batch_id,
                QualityResult.check_name == "duplicate_external_ids",
            )
        )

    assert account_count == 0
    assert persisted_passed is False


def test_failed_quality_check_preserves_raw_evidence(clean_engine) -> None:
    summary = ingest_fixture(FIXTURES / "negative_amount.json", clean_engine)
    outcomes = {result.check_name: result for result in summary.quality_results}

    assert summary.status is IngestionStatus.FAILED
    assert not outcomes["negative_amounts"].passed

    with clean_engine.connect() as connection:
        batch_status = connection.scalar(
            select(IngestionBatch.status).where(IngestionBatch.batch_id == summary.batch_id)
        )
        raw_count = connection.scalar(
            select(func.count())
            .select_from(RawFinancialRecord)
            .where(RawFinancialRecord.batch_id == summary.batch_id)
        )
        account_count = connection.scalar(select(func.count()).select_from(Account))

    assert batch_status is IngestionStatus.FAILED
    assert raw_count == 6
    assert account_count == 0


def test_retry_after_injected_failure_reuses_staged_batch(clean_engine) -> None:
    injector = FailureInjector(frozenset({FailurePoint.AFTER_RAW_STAGE}))

    failed = ingest_fixture(
        FIXTURES / "synthetic_financial.json",
        clean_engine,
        failure_injector=injector,
    )
    retried = ingest_fixture(FIXTURES / "synthetic_financial.json", clean_engine)

    assert failed.status is IngestionStatus.FAILED
    assert failed.attempt_number == 1
    assert retried.status is IngestionStatus.SUCCEEDED
    assert retried.batch_id == failed.batch_id
    assert retried.attempt_number == 2
    assert retried.raw_records == 7

    with clean_engine.connect() as connection:
        attempts = connection.scalar(
            select(IngestionBatch.attempt_count).where(IngestionBatch.batch_id == retried.batch_id)
        )
        transitions = connection.scalar(
            select(func.count())
            .select_from(BatchStateHistory)
            .where(BatchStateHistory.batch_id == retried.batch_id)
        )

    assert attempts == 2
    assert transitions == 9


def test_duplicate_successful_batch_is_idempotent(clean_engine) -> None:
    first = ingest_fixture(FIXTURES / "synthetic_financial.json", clean_engine)
    duplicate = ingest_fixture(FIXTURES / "synthetic_financial.json", clean_engine)

    assert duplicate.idempotent is True
    assert duplicate.batch_id == first.batch_id
    assert duplicate.attempt_number == 1

    with clean_engine.connect() as connection:
        batch_count = connection.scalar(select(func.count()).select_from(IngestionBatch))
        raw_count = connection.scalar(select(func.count()).select_from(RawFinancialRecord))
        transaction_count = connection.scalar(
            select(func.count()).select_from(FinancialTransaction)
        )

    assert batch_count == 1
    assert raw_count == 7
    assert transaction_count == 2
    with clean_engine.connect() as connection:
        event_count = connection.scalar(select(func.count()).select_from(FinancialEvent))
    assert event_count == 4


def test_checkpoint_resumes_from_previous_success(clean_engine) -> None:
    first = ingest_fixture(FIXTURES / "synthetic_financial.json", clean_engine)
    resumed = ingest_fixture(
        FIXTURES / "synthetic_financial_incremental.json",
        clean_engine,
    )

    assert first.status is IngestionStatus.SUCCEEDED
    assert resumed.status is IngestionStatus.SUCCEEDED
    assert resumed.core_records == 3

    with clean_engine.connect() as connection:
        checkpoint = connection.execute(
            select(
                SourceCheckpoint.checkpoint_value,
                SourceCheckpoint.version,
                SourceCheckpoint.last_batch_id,
            ).where(SourceCheckpoint.source_name == "synthetic_bank_fixture")
        ).one()
        transaction_count = connection.scalar(
            select(func.count()).select_from(FinancialTransaction)
        )

    assert checkpoint.checkpoint_value == "2026-01-03T00:00:00Z"
    assert checkpoint.version == 2
    assert checkpoint.last_batch_id == resumed.batch_id
    assert transaction_count == 3


def test_nonblocking_quality_policy_allows_observational_failure(clean_engine, tmp_path) -> None:
    fixture = json.loads((FIXTURES / "synthetic_financial.json").read_text())
    fixture["source_name"] = "synthetic_observational_fixture"
    fixture["balances"][0]["amount"] = "8999.99"
    path = tmp_path / "observational.json"
    path.write_text(json.dumps(fixture))
    config = QualityConfig.default()
    config = QualityConfig(
        {
            **config.checks,
            "transaction_reconciliation": CheckPolicy(enabled=True, blocking=False),
        }
    )

    summary = ingest_fixture(path, clean_engine, quality_config=config)
    outcomes = {result.check_name: result for result in summary.quality_results}

    assert summary.status is IngestionStatus.SUCCEEDED
    assert not outcomes["transaction_reconciliation"].passed


def test_events_reconstruct_correct_balances(clean_engine) -> None:
    summary = ingest_fixture(FIXTURES / "synthetic_financial.json", clean_engine)

    with Session(clean_engine) as session:
        accounts = {
            account.account_id: account.external_id for account in session.scalars(select(Account))
        }
        assets = {asset.asset_id: asset.external_id for asset in session.scalars(select(Asset))}
        events = tuple(
            CanonicalEvent(
                external_id=event.external_id,
                event_type=event.event_type,
                occurred_at=event.occurred_at,
                asset_external_id=assets.get(event.asset_id),
                account_from_external_id=accounts.get(event.account_from_id),
                account_to_external_id=accounts.get(event.account_to_id),
                amount=event.amount,
                metadata=event.event_metadata,
            )
            for event in session.scalars(select(FinancialEvent))
        )

    balances = reconstruct_balances(events)

    assert summary.status is IngestionStatus.SUCCEEDED
    assert len(events) == 4
    assert balances[("ACC-100", "USD")] == 9000
    assert balances[("ACC-200", "USD")] == 6000


@pytest.mark.parametrize(
    ("fixture_name", "failed_invariant"),
    [
        ("event_create_money.json", "balance_conservation"),
        ("event_negative_transfer.json", "no_negative_balances"),
        ("event_missing_destination.json", "event_completeness"),
        ("event_balance_mismatch.json", "balance_snapshot_match"),
    ],
)
def test_invalid_event_sequences_fail_and_persist_results(
    clean_engine, fixture_name, failed_invariant
) -> None:
    summary = ingest_fixture(FIXTURES / fixture_name, clean_engine)
    outcomes = {result.name: result for result in summary.invariant_results}

    assert summary.status is IngestionStatus.FAILED
    assert not outcomes[failed_invariant].passed

    with clean_engine.connect() as connection:
        failed_count = connection.scalar(
            select(func.count())
            .select_from(InvariantResult)
            .where(
                InvariantResult.batch_id == summary.batch_id,
                InvariantResult.execution_result == "failed",
            )
        )
        event_count = connection.scalar(select(func.count()).select_from(FinancialEvent))
        checkpoint_count = connection.scalar(select(func.count()).select_from(SourceCheckpoint))

    assert failed_count >= 1
    assert event_count == 0
    assert checkpoint_count == 0


def test_retry_after_invariant_stage_failure(clean_engine) -> None:
    injector = FailureInjector(frozenset({FailurePoint.BEFORE_INVARIANT_CHECK}))

    failed = ingest_fixture(
        FIXTURES / "synthetic_financial.json",
        clean_engine,
        failure_injector=injector,
    )
    retried = ingest_fixture(FIXTURES / "synthetic_financial.json", clean_engine)

    assert failed.status is IngestionStatus.FAILED
    assert retried.status is IngestionStatus.SUCCEEDED
    assert retried.batch_id == failed.batch_id
    assert retried.attempt_number == 2

    with clean_engine.connect() as connection:
        event_count = connection.scalar(select(func.count()).select_from(FinancialEvent))
        invariant_count = connection.scalar(
            select(func.count())
            .select_from(InvariantResult)
            .where(InvariantResult.batch_id == retried.batch_id)
        )

    assert event_count == 4
    assert invariant_count == 4


def test_retry_after_invariant_violation_reuses_batch_and_raw_data(clean_engine) -> None:
    first = ingest_fixture(FIXTURES / "event_missing_destination.json", clean_engine)
    retried = ingest_fixture(FIXTURES / "event_missing_destination.json", clean_engine)

    assert first.status is IngestionStatus.FAILED
    assert retried.status is IngestionStatus.FAILED
    assert retried.batch_id == first.batch_id
    assert retried.attempt_number == 2
    assert retried.raw_records == 6

    with clean_engine.connect() as connection:
        invariant_count = connection.scalar(
            select(func.count())
            .select_from(InvariantResult)
            .where(InvariantResult.batch_id == retried.batch_id)
        )
        raw_count = connection.scalar(
            select(func.count())
            .select_from(RawFinancialRecord)
            .where(RawFinancialRecord.batch_id == retried.batch_id)
        )

    assert invariant_count == 8
    assert raw_count == 6
