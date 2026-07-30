import hashlib
import json
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from sentinel.database import create_database_engine, session_scope
from sentinel.ingestion.events import build_candidate_events
from sentinel.ingestion.failures import (
    FailureInjector,
    FailurePoint,
    InjectedPipelineFailure,
)
from sentinel.ingestion.lifecycle import record_initial_state, transition_batch
from sentinel.models import (
    Account,
    AnalysisStatus,
    Asset,
    Balance,
    FinancialEvent,
    FinancialTransaction,
    IncidentOrigin,
    IngestionBatch,
    IngestionStatus,
    InvariantResult,
    QualityResult,
    RawEthereumBlock,
    RawEthereumLog,
    RawEthereumTransaction,
    RawEthereumTransfer,
    RawFinancialRecord,
    SourceCheckpoint,
)
from sentinel.protocols.base import ProtocolPlugin
from sentinel.quality import CheckOutcome, QualityConfig, run_quality_checks
from sentinel.security import (
    CanonicalEvent,
    EvaluationScope,
    InvariantContext,
    InvariantExecutionResult,
    InvariantOutcome,
    record_invariant_incidents,
    resolve_batch_incidents,
    run_invariants,
)

COLLECTION_MODEL = {
    "accounts": Account,
    "assets": Asset,
    "transactions": FinancialTransaction,
    "balances": Balance,
}
RAW_COLLECTIONS = (*COLLECTION_MODEL, "events")


class BatchInProgressError(RuntimeError):
    pass


@dataclass(frozen=True)
class IngestionSummary:
    batch_id: uuid.UUID
    status: IngestionStatus
    raw_records: int
    core_records: int
    quality_results: tuple[CheckOutcome, ...]
    invariant_results: tuple[InvariantOutcome, ...]
    attempt_number: int
    analysis_status: AnalysisStatus
    idempotent: bool = False


def _load_fixture(path: Path) -> tuple[dict[str, Any], bytes]:
    content = path.read_bytes()
    fixture = json.loads(content)
    if not isinstance(fixture, dict):
        raise ValueError("Fixture root must be a JSON object")
    return fixture, content


def _all_records(fixture: Mapping[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    records: list[tuple[str, dict[str, Any]]] = []
    for collection in RAW_COLLECTIONS:
        value = fixture.get(collection, [])
        if isinstance(value, list):
            records.extend((collection, record) for record in value if isinstance(record, dict))
    return records


def _existing_external_ids(fixture: Mapping[str, Any], session: Session) -> dict[str, set[str]]:
    source_name = str(fixture.get("source_name", ""))
    existing: dict[str, set[str]] = {}
    for collection, model in COLLECTION_MODEL.items():
        values = session.scalars(select(model.external_id).where(model.source_name == source_name))
        existing[collection] = set(values)
    existing["events"] = set(
        session.scalars(
            select(FinancialEvent.external_id).where(
                FinancialEvent.source_system == source_name,
                FinancialEvent.canonical.is_(True),
            )
        )
    )
    return existing


def _current_checkpoint(source_name: str, session: Session) -> str | None:
    return session.scalar(
        select(SourceCheckpoint.checkpoint_value).where(SourceCheckpoint.source_name == source_name)
    )


def _parse_datetime(value: object) -> datetime:
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def _quality_model(
    batch_id: uuid.UUID,
    attempt_number: int,
    outcome: CheckOutcome,
    blocking: bool,
) -> QualityResult:
    return QualityResult(
        result_id=uuid.uuid4(),
        batch_id=batch_id,
        attempt_number=attempt_number,
        check_name=outcome.check_name,
        passed=outcome.passed,
        records_checked=outcome.records_checked,
        failure_count=outcome.failure_count,
        details={**outcome.details, "blocking": blocking},
    )


def _invariant_model(
    batch_id: uuid.UUID,
    attempt_number: int,
    outcome: InvariantOutcome,
    origin: IncidentOrigin,
    case_id: uuid.UUID | None,
) -> InvariantResult:
    return InvariantResult(
        invariant_id=uuid.uuid4(),
        batch_id=batch_id,
        attempt_number=attempt_number,
        name=outcome.name,
        protocol_name=outcome.protocol_name,
        origin=origin,
        case_id=case_id,
        severity=outcome.severity,
        description=outcome.description,
        execution_result=outcome.execution_result,
        affected_records=list(outcome.affected_records),
    )


def _checkpoint_outcome(
    fixture: Mapping[str, Any], current_checkpoint: str | None
) -> CheckOutcome | None:
    previous = fixture.get("previous_checkpoint")
    if current_checkpoint is None and previous in (None, ""):
        return None
    if previous == current_checkpoint:
        return None
    return CheckOutcome(
        check_name="checkpoint_continuity",
        records_checked=1,
        failures=(
            {
                "expected_previous_checkpoint": current_checkpoint,
                "received_previous_checkpoint": previous,
            },
        ),
    )


def _load_core(
    fixture: Mapping[str, Any],
    batch_id: uuid.UUID,
    session: Session,
    candidate_events: tuple[CanonicalEvent, ...],
) -> int:
    source_name = str(fixture["source_name"])
    account_ids = {
        account.external_id: account.account_id
        for account in session.scalars(select(Account).where(Account.source_name == source_name))
    }
    asset_ids = {
        asset.external_id: asset.asset_id
        for asset in session.scalars(select(Asset).where(Asset.source_name == source_name))
    }

    for record in fixture["accounts"]:
        account_id = uuid.uuid4()
        external_id = str(record["external_id"])
        account_ids[external_id] = account_id
        session.add(
            Account(
                account_id=account_id,
                source_name=source_name,
                external_id=external_id,
                name=str(record["name"]),
                account_type=str(record["account_type"]),
            )
        )

    for record in fixture["assets"]:
        asset_id = uuid.uuid4()
        external_id = str(record["external_id"])
        asset_ids[external_id] = asset_id
        session.add(
            Asset(
                asset_id=asset_id,
                source_name=source_name,
                external_id=external_id,
                symbol=str(record["symbol"]),
                name=str(record["name"]),
                asset_type=str(record["asset_type"]),
                decimals=int(record["decimals"]),
            )
        )

    for record in fixture["transactions"]:
        session.add(
            FinancialTransaction(
                transaction_id=uuid.uuid4(),
                batch_id=batch_id,
                source_name=source_name,
                external_id=str(record["external_id"]),
                from_account_id=account_ids[str(record["from_account_external_id"])],
                to_account_id=account_ids[str(record["to_account_external_id"])],
                asset_id=asset_ids[str(record["asset_external_id"])],
                amount=Decimal(str(record["amount"])),
                occurred_at=_parse_datetime(record["occurred_at"]),
                description=record.get("description"),
            )
        )

    for record in fixture["balances"]:
        session.add(
            Balance(
                balance_id=uuid.uuid4(),
                batch_id=batch_id,
                source_name=source_name,
                external_id=str(record["external_id"]),
                account_id=account_ids[str(record["account_external_id"])],
                asset_id=asset_ids[str(record["asset_external_id"])],
                amount=Decimal(str(record["amount"])),
                as_of=_parse_datetime(record["as_of"]),
            )
        )

    for event in candidate_events:
        session.add(
            FinancialEvent(
                event_id=uuid.uuid4(),
                batch_id=batch_id,
                source_system=source_name,
                external_id=event.external_id,
                chain_id=(event.chain_id if event.chain_id is not None else None),
                block_number=event.block_number,
                transaction_index=event.transaction_index,
                log_index=event.log_index,
                block_hash=event.block_hash,
                canonical=True,
                checker_authorized=event.checker_authorized,
                event_type=event.event_type,
                occurred_at=event.occurred_at,
                asset_id=(
                    asset_ids.get(event.asset_external_id)
                    if event.asset_external_id is not None
                    else None
                ),
                account_from_id=(
                    account_ids.get(event.account_from_external_id)
                    if event.account_from_external_id is not None
                    else None
                ),
                account_to_id=(
                    account_ids.get(event.account_to_external_id)
                    if event.account_to_external_id is not None
                    else None
                ),
                amount=event.amount,
                event_metadata=event.metadata,
            )
        )

    return sum(
        len(value)
        for collection in RAW_COLLECTIONS
        if isinstance((value := fixture.get(collection, [])), list)
    )


def _existing_canonical_events(source_name: str, session: Session) -> tuple[CanonicalEvent, ...]:
    account_external_ids = {
        account.account_id: account.external_id
        for account in session.scalars(select(Account).where(Account.source_name == source_name))
    }
    asset_external_ids = {
        asset.asset_id: asset.external_id
        for asset in session.scalars(select(Asset).where(Asset.source_name == source_name))
    }
    return tuple(
        CanonicalEvent(
            external_id=event.external_id,
            event_type=event.event_type,
            occurred_at=event.occurred_at,
            asset_external_id=(
                asset_external_ids.get(event.asset_id)
                or (
                    str(event.event_metadata["token_address"])
                    if event.event_metadata.get("token_address") is not None
                    else None
                )
            ),
            account_from_external_id=account_external_ids.get(event.account_from_id),
            account_to_external_id=account_external_ids.get(event.account_to_id),
            amount=event.amount,
            metadata=event.event_metadata,
            chain_id=event.chain_id,
            block_number=event.block_number,
            block_hash=event.block_hash,
            transaction_index=event.transaction_index,
            log_index=event.log_index,
            checker_authorized=event.checker_authorized,
        )
        for event in session.scalars(
            select(FinancialEvent).where(
                FinancialEvent.source_system == source_name,
                FinancialEvent.canonical.is_(True),
            )
        )
    )


def _prepare_batch(
    engine: Engine,
    source_name: str,
    checksum: str,
    analysis_status: AnalysisStatus,
    origin: IncidentOrigin,
    case_id: uuid.UUID | None,
) -> tuple[uuid.UUID, int, bool, bool]:
    with session_scope(engine) as session:
        existing = session.scalar(
            select(IngestionBatch).where(
                IngestionBatch.source_name == source_name,
                IngestionBatch.checksum == checksum,
            )
        )
        if existing is not None:
            if existing.status is IngestionStatus.SUCCEEDED:
                return existing.batch_id, existing.attempt_count, True, False
            if existing.status is not IngestionStatus.FAILED:
                raise BatchInProgressError(
                    f"Batch {existing.batch_id} is already {existing.status.value}"
                )
            existing.attempt_count += 1
            existing.analysis_status = analysis_status
            existing.finished_at = None
            existing.rows_loaded = 0
            transition_batch(
                session,
                existing,
                IngestionStatus.RUNNING,
                {"reason": "retry"},
            )
            return existing.batch_id, existing.attempt_count, False, True

        batch = IngestionBatch(
            batch_id=uuid.uuid4(),
            source_name=source_name,
            started_at=datetime.now(UTC),
            status=IngestionStatus.RUNNING,
            rows_loaded=0,
            checksum=checksum,
            attempt_count=1,
            analysis_status=analysis_status,
            origin=origin,
            case_id=case_id,
        )
        session.add(batch)
        session.flush()
        record_initial_state(session, batch)
        return batch.batch_id, batch.attempt_count, False, False


def _mark_failed(
    engine: Engine,
    batch_id: uuid.UUID,
    details: Mapping[str, object],
) -> None:
    with session_scope(engine) as session:
        batch = session.get(IngestionBatch, batch_id)
        if batch is None or batch.status in {IngestionStatus.FAILED, IngestionStatus.SUCCEEDED}:
            return
        transition_batch(session, batch, IngestionStatus.FAILED, details)
        batch.finished_at = datetime.now(UTC)


def _persist_checkpoint(
    fixture: Mapping[str, Any],
    batch_id: uuid.UUID,
    session: Session,
) -> None:
    checkpoint_value = fixture.get("checkpoint")
    if checkpoint_value in (None, ""):
        return
    source_name = str(fixture["source_name"])
    checkpoint = session.scalar(
        select(SourceCheckpoint).where(SourceCheckpoint.source_name == source_name)
    )
    if checkpoint is None:
        session.add(
            SourceCheckpoint(
                checkpoint_id=uuid.uuid4(),
                source_name=source_name,
                checkpoint_value=str(checkpoint_value),
                chain_id=fixture.get("checkpoint_chain_id"),
                source_identity=fixture.get("checkpoint_source_identity"),
                block_number=fixture.get("checkpoint_block_number"),
                block_hash=fixture.get("checkpoint_block_hash"),
                last_batch_id=batch_id,
                version=1,
            )
        )
        return
    checkpoint.checkpoint_value = str(checkpoint_value)
    checkpoint.chain_id = fixture.get("checkpoint_chain_id")
    checkpoint.source_identity = fixture.get("checkpoint_source_identity")
    checkpoint.block_number = fixture.get("checkpoint_block_number")
    checkpoint.block_hash = fixture.get("checkpoint_block_hash")
    checkpoint.last_batch_id = batch_id
    checkpoint.version += 1
    checkpoint.updated_at = datetime.now(UTC)


def _stored_summary(
    engine: Engine,
    batch_id: uuid.UUID,
    *,
    idempotent: bool = False,
) -> IngestionSummary:
    with session_scope(engine) as session:
        batch = session.get(IngestionBatch, batch_id)
        if batch is None:
            raise RuntimeError(f"Ingestion batch {batch_id} was not persisted")
        results = session.scalars(
            select(QualityResult)
            .where(
                QualityResult.batch_id == batch_id,
                QualityResult.attempt_number == batch.attempt_count,
            )
            .order_by(QualityResult.created_at, QualityResult.check_name)
        )
        outcomes = tuple(
            CheckOutcome(
                check_name=result.check_name,
                records_checked=result.records_checked,
                failures=tuple(result.details.get("failures", [])),
            )
            for result in results
        )
        invariant_rows = session.scalars(
            select(InvariantResult)
            .where(
                InvariantResult.batch_id == batch_id,
                InvariantResult.attempt_number == batch.attempt_count,
            )
            .order_by(InvariantResult.created_at, InvariantResult.name)
        )
        invariant_outcomes = tuple(
            InvariantOutcome(
                name=result.name,
                protocol_name=result.protocol_name,
                severity=result.severity,
                description=result.description,
                affected_records=tuple(result.affected_records),
                result=InvariantExecutionResult(result.execution_result),
            )
            for result in invariant_rows
        )
        financial_raw_records = session.scalar(
            select(func.count())
            .select_from(RawFinancialRecord)
            .where(RawFinancialRecord.batch_id == batch_id)
        )
        ethereum_transactions = session.scalar(
            select(func.count())
            .select_from(RawEthereumTransaction)
            .where(RawEthereumTransaction.batch_id == batch_id)
        )
        ethereum_transfers = session.scalar(
            select(func.count())
            .select_from(RawEthereumTransfer)
            .where(RawEthereumTransfer.batch_id == batch_id)
        )
        ethereum_blocks = session.scalar(
            select(func.count())
            .select_from(RawEthereumBlock)
            .where(RawEthereumBlock.batch_id == batch_id)
        )
        ethereum_logs = session.scalar(
            select(func.count())
            .select_from(RawEthereumLog)
            .where(RawEthereumLog.batch_id == batch_id)
        )
        return IngestionSummary(
            batch_id=batch.batch_id,
            status=batch.status,
            raw_records=sum(
                (
                    financial_raw_records or 0,
                    ethereum_blocks or 0,
                    ethereum_logs or 0,
                    ethereum_transactions or 0,
                    ethereum_transfers or 0,
                )
            ),
            core_records=batch.rows_loaded,
            quality_results=outcomes,
            invariant_results=invariant_outcomes,
            attempt_number=batch.attempt_count,
            analysis_status=batch.analysis_status,
            idempotent=idempotent,
        )


def ingest_fixture(
    path: Path,
    engine: Engine | None = None,
    *,
    quality_config: QualityConfig | None = None,
    failure_injector: FailureInjector | None = None,
) -> IngestionSummary:
    """Run a retry-safe raw-to-core pipeline for one JSON fixture."""
    fixture, content = _load_fixture(path)
    return ingest_fixture_payload(
        fixture,
        content,
        engine,
        quality_config=quality_config,
        failure_injector=failure_injector,
    )


def ingest_fixture_payload(
    fixture: dict[str, Any],
    source_content: bytes,
    engine: Engine | None = None,
    *,
    quality_config: QualityConfig | None = None,
    failure_injector: FailureInjector | None = None,
    raw_stager: Callable[[Session, uuid.UUID], None] | None = None,
    stage_financial_records: bool = True,
    protocol_plugin: ProtocolPlugin | None = None,
    protocol_source: Mapping[str, Any] | None = None,
    analysis_status: AnalysisStatus = AnalysisStatus.SUPPORTED,
    origin: IncidentOrigin = IncidentOrigin.FIXTURE,
    case_id: uuid.UUID | None = None,
    invariant_context: InvariantContext | None = None,
) -> IngestionSummary:
    """Run the shared pipeline for an adapter-normalized fixture payload."""
    engine = engine or create_database_engine()
    quality_config = quality_config or QualityConfig.default()
    failure_injector = failure_injector or FailureInjector()
    source_name = str(fixture.get("source_name", "unknown"))
    logical_identity = (
        source_content
        + b"\0"
        + origin.value.encode()
        + b"\0"
        + (str(case_id).encode() if case_id is not None else b"-")
    )
    checksum = hashlib.sha256(logical_identity).hexdigest()
    records = _all_records(fixture)
    batch_id, attempt, idempotent, retry = _prepare_batch(
        engine,
        source_name,
        checksum,
        analysis_status,
        origin,
        case_id,
    )

    if idempotent:
        return _stored_summary(engine, batch_id, idempotent=True)

    try:
        with session_scope(engine) as session:
            batch = session.get(IngestionBatch, batch_id)
            if batch is None:
                raise RuntimeError(f"Ingestion batch {batch_id} was not persisted")
            if not retry and stage_financial_records:
                session.add_all(
                    RawFinancialRecord(
                        record_id=uuid.uuid4(),
                        batch_id=batch_id,
                        source_name=source_name,
                        record_type=collection,
                        external_id=(
                            str(record["external_id"])
                            if record.get("external_id") is not None
                            else None
                        ),
                        payload=record,
                    )
                    for collection, record in records
                )
            if not retry and raw_stager is not None:
                raw_stager(session, batch_id)
            transition_batch(session, batch, IngestionStatus.STAGED)

        failure_injector.trigger(FailurePoint.AFTER_RAW_STAGE)

        if analysis_status is AnalysisStatus.UNSUPPORTED:
            with session_scope(engine) as session:
                batch = session.get(IngestionBatch, batch_id)
                if batch is None:
                    raise RuntimeError(f"Ingestion batch {batch_id} was not persisted")
                transition_batch(
                    session,
                    batch,
                    IngestionStatus.FAILED,
                    {"reason": "unsupported_analysis"},
                )
                batch.finished_at = datetime.now(UTC)
            return _stored_summary(engine, batch_id)

        with session_scope(engine) as session:
            batch = session.get(IngestionBatch, batch_id)
            if batch is None:
                raise RuntimeError(f"Ingestion batch {batch_id} was not persisted")
            transition_batch(session, batch, IngestionStatus.VALIDATING)

        failure_injector.trigger(FailurePoint.BEFORE_VALIDATION)

        with session_scope(engine) as session:
            existing_ids = _existing_external_ids(fixture, session)
            checkpoint = _current_checkpoint(source_name, session)
        outcomes = list(run_quality_checks(fixture, existing_ids, quality_config.enabled_checks))
        checkpoint_outcome = _checkpoint_outcome(fixture, checkpoint)
        if checkpoint_outcome is not None:
            outcomes.append(checkpoint_outcome)

        failure_injector.trigger(FailurePoint.AFTER_VALIDATION)

        validation_passed = all(
            outcome.passed or not quality_config.is_blocking(outcome.check_name)
            for outcome in outcomes
        )
        with session_scope(engine) as session:
            batch = session.get(IngestionBatch, batch_id)
            if batch is None:
                raise RuntimeError(f"Ingestion batch {batch_id} was not persisted")
            session.add_all(
                _quality_model(
                    batch_id,
                    attempt,
                    outcome,
                    quality_config.is_blocking(outcome.check_name),
                )
                for outcome in outcomes
            )
            if validation_passed:
                transition_batch(session, batch, IngestionStatus.LOADING)
            else:
                transition_batch(
                    session,
                    batch,
                    IngestionStatus.FAILED,
                    {"reason": "blocking_quality_check"},
                )
                batch.finished_at = datetime.now(UTC)

        if not validation_passed:
            return _stored_summary(engine, batch_id)

        candidate_events = build_candidate_events(
            fixture,
            has_previous_checkpoint=checkpoint is not None,
        )
        event_blocks = tuple(
            event.block_number for event in candidate_events if event.block_number is not None
        )
        system_authorized_event_ids = frozenset(
            event.external_id for event in candidate_events if event.checker_authorized
        )
        if invariant_context is None:
            context = InvariantContext(
                source_system=source_name,
                chain_id=next(
                    (event.chain_id for event in candidate_events if event.chain_id is not None),
                    None,
                ),
                block_range=(min(event_blocks), max(event_blocks)) if event_blocks else None,
                evaluation_scope=EvaluationScope.FULL_STATE,
                system_authorized_event_ids=system_authorized_event_ids,
            )
        else:
            context = replace(
                invariant_context,
                block_range=(
                    invariant_context.block_range
                    or ((min(event_blocks), max(event_blocks)) if event_blocks else None)
                ),
                system_authorized_event_ids=(
                    invariant_context.system_authorized_event_ids | system_authorized_event_ids
                ),
            )
        with session_scope(engine) as session:
            existing_events = _existing_canonical_events(source_name, session)
            batch = session.get(IngestionBatch, batch_id)
            if batch is None:
                raise RuntimeError(f"Ingestion batch {batch_id} was not persisted")
            transition_batch(session, batch, IngestionStatus.INVARIANT_CHECKING)

        context = replace(
            context,
            system_authorized_event_ids=(
                context.system_authorized_event_ids
                | frozenset(
                    event.external_id for event in existing_events if event.checker_authorized
                )
            ),
        )

        failure_injector.trigger(FailurePoint.BEFORE_INVARIANT_CHECK)

        global_outcomes = run_invariants(
            (*existing_events, *candidate_events),
            fixture.get("balances", []),
            context,
        )
        protocol_outcomes = (
            protocol_plugin.invariants(
                (*existing_events, *candidate_events),
                protocol_source or {},
                context,
            )
            if protocol_plugin is not None
            else ()
        )
        invariant_outcomes = (*global_outcomes, *protocol_outcomes)
        invariants_passed = all(
            not outcome.failed
            or (
                outcome.name == "balance_snapshot_match"
                and not quality_config.is_blocking("transaction_reconciliation")
            )
            for outcome in invariant_outcomes
        )

        with session_scope(engine) as session:
            batch = session.get(IngestionBatch, batch_id)
            if batch is None:
                raise RuntimeError(f"Ingestion batch {batch_id} was not persisted")
            session.add_all(
                _invariant_model(batch_id, attempt, outcome, origin, case_id)
                for outcome in invariant_outcomes
            )
            record_invariant_incidents(
                session,
                batch_id,
                attempt,
                invariant_outcomes,
                origin=origin,
                case_id=case_id,
            )
            if not invariants_passed:
                transition_batch(
                    session,
                    batch,
                    IngestionStatus.FAILED,
                    {"reason": "invariant_violation"},
                )
                batch.finished_at = datetime.now(UTC)

        if not invariants_passed:
            return _stored_summary(engine, batch_id)

        failure_injector.trigger(FailurePoint.AFTER_INVARIANT_CHECK)
        failure_injector.trigger(FailurePoint.BEFORE_CORE_LOAD)

        with session_scope(engine) as session:
            batch = session.get(IngestionBatch, batch_id)
            if batch is None:
                raise RuntimeError(f"Ingestion batch {batch_id} was not persisted")
            core_records = _load_core(fixture, batch_id, session, candidate_events)
            _persist_checkpoint(fixture, batch_id, session)
            resolve_batch_incidents(session, batch_id, origin=origin, case_id=case_id)
            failure_injector.trigger(FailurePoint.AFTER_CORE_LOAD)
            batch.rows_loaded = core_records
            batch.finished_at = datetime.now(UTC)
            transition_batch(session, batch, IngestionStatus.SUCCEEDED)

        return _stored_summary(engine, batch_id)
    except InjectedPipelineFailure as error:
        _mark_failed(
            engine,
            batch_id,
            {"reason": "injected_failure", "failure_point": error.point.value},
        )
        return _stored_summary(engine, batch_id)
    except Exception as error:
        _mark_failed(engine, batch_id, {"reason": "unexpected_error", "error": str(error)})
        raise
