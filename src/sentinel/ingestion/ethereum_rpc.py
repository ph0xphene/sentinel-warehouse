import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from sentinel.config import Settings, get_settings
from sentinel.database import create_database_engine, session_scope
from sentinel.ethereum import EthereumBlock, EthereumLog, EthereumRPC, HttpEthereumRPC
from sentinel.ingestion.ethereum import _normalize_source, _raw_stager
from sentinel.ingestion.failures import FailureInjector
from sentinel.ingestion.fixture import IngestionSummary, ingest_fixture_payload
from sentinel.models import (
    AnalysisStatus,
    FinancialEvent,
    Incident,
    IncidentEvidence,
    IncidentOrigin,
    IncidentStatus,
    RawEthereumBlock,
    RawEthereumLog,
    RawEthereumTransaction,
    RawEthereumTransfer,
    RawFinancialRecord,
    SourceCheckpoint,
)
from sentinel.protocols import ProtocolNormalization, ProtocolPlugin, detect_rpc_protocol
from sentinel.quality import QualityConfig
from sentinel.security import EvaluationScope, InvariantContext


class EthereumRPCIngestionError(RuntimeError):
    """Base class for bounded historical ingestion errors."""


class ChainIDMismatchError(EthereumRPCIngestionError):
    pass


class FinalizedRangeError(EthereumRPCIngestionError):
    pass


class DeepReorganizationError(EthereumRPCIngestionError):
    pass


@dataclass(frozen=True)
class EthereumChainConfig:
    chain_id: int
    chain_name: str
    rpc_url: str
    request_timeout_seconds: float
    max_retries: int
    confirmation_depth: int
    max_block_range: int
    reorg_lookback: int

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> "EthereumChainConfig":
        values = settings or get_settings()
        return cls(
            chain_id=values.ethereum_chain_id,
            chain_name=values.ethereum_chain_name,
            rpc_url=values.ethereum_rpc_url,
            request_timeout_seconds=values.ethereum_rpc_timeout_seconds,
            max_retries=values.ethereum_rpc_max_retries,
            confirmation_depth=values.ethereum_confirmation_depth,
            max_block_range=values.ethereum_max_block_range,
            reorg_lookback=values.ethereum_reorg_lookback,
        )


@dataclass(frozen=True)
class ReorganizationSummary:
    detected_at_block: int
    common_ancestor: int
    orphaned_block_count: int


@dataclass(frozen=True)
class EthereumRPCIngestionSummary:
    pipeline: IngestionSummary
    chain_id: int
    chain_name: str
    contract_address: str
    requested_from_block: int | None
    requested_to_block: int
    from_block: int
    to_block: int
    finalized_boundary: int
    range_truncated: bool
    processed_chunks: int
    observed_logs: int
    normalized_events: int
    checkpoint_before: str | None
    checkpoint_after: str | None
    reorganization: ReorganizationSummary | None = None


def _source_name(chain_id: int, contract_address: str) -> str:
    return f"ethereum-rpc:{chain_id}:{contract_address.lower()}"


def _checkpoint_value(block_number: int, block_hash: str) -> str:
    return f"{block_number}:{block_hash.lower()}"


def _chunk_ranges(from_block: int, to_block: int, maximum_size: int) -> tuple[tuple[int, int], ...]:
    return tuple(
        (start, min(to_block, start + maximum_size - 1))
        for start in range(from_block, to_block + 1, maximum_size)
    )


def _timestamp(block: EthereumBlock) -> datetime:
    return datetime.fromtimestamp(block.timestamp, UTC)


def _block_payload(block: EthereumBlock) -> dict[str, object]:
    return {
        "block_number": block.number,
        "block_hash": block.block_hash,
        "parent_hash": block.parent_hash,
        "block_timestamp": _timestamp(block).isoformat(),
    }


def _log_payload(log: EthereumLog, block: EthereumBlock) -> dict[str, object]:
    return {
        "address": log.address,
        "topics": list(log.topics),
        "data": log.data,
        "block_number": log.block_number,
        "block_hash": log.block_hash,
        "transaction_hash": log.transaction_hash,
        "transaction_index": log.transaction_index,
        "log_index": log.log_index,
        "removed": log.removed,
        "block_timestamp": _timestamp(block).isoformat(),
    }


def _load_checkpoint(engine: Engine, source_name: str) -> SourceCheckpoint | None:
    with Session(engine) as session:
        checkpoint = session.scalar(
            select(SourceCheckpoint).where(SourceCheckpoint.source_name == source_name)
        )
        if checkpoint is not None:
            session.expunge(checkpoint)
        return checkpoint


def _record_deep_reorg_incident(
    engine: Engine,
    checkpoint: SourceCheckpoint,
    *,
    configured_chain_id: int,
    current_hash: str,
    lookback: int,
) -> None:
    with session_scope(engine) as session:
        incident = session.scalar(
            select(Incident).where(
                Incident.batch_id == checkpoint.last_batch_id,
                Incident.incident_type == "ethereum_deep_reorganization",
                Incident.origin == IncidentOrigin.LIVE,
            )
        )
        if incident is not None:
            if incident.status is IncidentStatus.RESOLVED:
                incident.status = IncidentStatus.OPEN
            return
        incident = Incident(
            incident_id=uuid.uuid4(),
            incident_type="ethereum_deep_reorganization",
            protocol_name=None,
            origin=IncidentOrigin.LIVE,
            case_id=None,
            severity="critical",
            status=IncidentStatus.OPEN,
            detected_at=datetime.now(UTC),
            batch_id=checkpoint.last_batch_id,
            summary=(
                "Ethereum checkpoint diverged and no common ancestor was found "
                f"within {lookback} blocks."
            ),
        )
        session.add(incident)
        session.flush()
        session.add(
            IncidentEvidence(
                evidence_id=uuid.uuid4(),
                incident_id=incident.incident_id,
                affected_entity=checkpoint.source_name,
                evidence_type="chain_reorganization",
                origin=IncidentOrigin.LIVE,
                payload={
                    "chain_id": configured_chain_id,
                    "checkpoint_block_number": checkpoint.block_number,
                    "checkpoint_block_hash": checkpoint.block_hash,
                    "current_block_hash": current_hash,
                    "lookback": lookback,
                },
            )
        )


async def _handle_reorganization(
    rpc: EthereumRPC,
    engine: Engine,
    checkpoint: SourceCheckpoint | None,
    config: EthereumChainConfig,
) -> ReorganizationSummary | None:
    if (
        checkpoint is None
        or checkpoint.block_number is None
        or checkpoint.block_hash is None
        or checkpoint.chain_id is None
    ):
        return None

    current = await rpc.get_block_by_number(checkpoint.block_number)
    if current.block_hash == checkpoint.block_hash.lower():
        return None

    minimum = max(0, checkpoint.block_number - config.reorg_lookback)
    with Session(engine) as session:
        observed = {
            block.block_number: block.block_hash
            for block in session.scalars(
                select(RawEthereumBlock)
                .where(
                    RawEthereumBlock.source_name == checkpoint.source_name,
                    RawEthereumBlock.chain_id == checkpoint.chain_id,
                    RawEthereumBlock.block_number.between(minimum, checkpoint.block_number - 1),
                    RawEthereumBlock.canonical.is_(True),
                )
                .order_by(RawEthereumBlock.observed_at.desc())
            )
        }

    ancestor: EthereumBlock | None = None
    for block_number in range(checkpoint.block_number - 1, minimum - 1, -1):
        expected_hash = observed.get(block_number)
        if expected_hash is None:
            continue
        candidate = await rpc.get_block_by_number(block_number)
        if candidate.block_hash == expected_hash:
            ancestor = candidate
            break

    if ancestor is None:
        _record_deep_reorg_incident(
            engine,
            checkpoint,
            configured_chain_id=config.chain_id,
            current_hash=current.block_hash,
            lookback=config.reorg_lookback,
        )
        raise DeepReorganizationError(
            "No common Ethereum ancestor found within configured reorganization lookback"
        )

    with session_scope(engine) as session:
        orphaned_blocks = tuple(
            session.scalars(
                select(RawEthereumBlock).where(
                    RawEthereumBlock.source_name == checkpoint.source_name,
                    RawEthereumBlock.chain_id == checkpoint.chain_id,
                    RawEthereumBlock.block_number > ancestor.number,
                    RawEthereumBlock.canonical.is_(True),
                )
            )
        )
        orphaned_hashes = {block.block_hash for block in orphaned_blocks}
        for block in orphaned_blocks:
            block.canonical = False
        if orphaned_hashes:
            for model in (RawEthereumLog, RawFinancialRecord):
                for record in session.scalars(
                    select(model).where(
                        model.source_name == checkpoint.source_name,
                        model.block_hash.in_(orphaned_hashes),
                        model.canonical.is_(True),
                    )
                ):
                    record.canonical = False
            for model in (RawEthereumTransaction, RawEthereumTransfer):
                for record in session.scalars(
                    select(model).where(
                        model.chain_id == checkpoint.chain_id,
                        model.block_hash.in_(orphaned_hashes),
                        model.canonical.is_(True),
                    )
                ):
                    record.canonical = False
            for event in session.scalars(
                select(FinancialEvent).where(
                    FinancialEvent.source_system == checkpoint.source_name,
                    FinancialEvent.block_hash.in_(orphaned_hashes),
                    FinancialEvent.canonical.is_(True),
                )
            ):
                event.canonical = False

        stored = session.scalar(
            select(SourceCheckpoint).where(
                SourceCheckpoint.checkpoint_id == checkpoint.checkpoint_id
            )
        )
        if stored is None:
            raise RuntimeError("Ethereum checkpoint disappeared during reorganization handling")
        stored.block_number = ancestor.number
        stored.block_hash = ancestor.block_hash
        stored.checkpoint_value = _checkpoint_value(ancestor.number, ancestor.block_hash)
        stored.version += 1
        stored.updated_at = datetime.now(UTC)

    return ReorganizationSummary(
        detected_at_block=checkpoint.block_number,
        common_ancestor=ancestor.number,
        orphaned_block_count=len(orphaned_blocks),
    )


def _rpc_raw_stager(
    source: dict[str, Any],
    protocol: ProtocolNormalization,
    blocks: tuple[EthereumBlock, ...],
    logs: tuple[EthereumLog, ...],
):
    protocol_stage = _raw_stager(source, protocol)
    source_name = str(source["source_name"])
    chain_id = int(source["chain_id"])

    def stage(session: Session, batch_id: uuid.UUID) -> None:
        session.add_all(
            RawEthereumBlock(
                observation_id=uuid.uuid4(),
                batch_id=batch_id,
                source_name=source_name,
                chain_id=chain_id,
                block_number=block.number,
                block_hash=block.block_hash,
                parent_hash=block.parent_hash,
                block_timestamp=_timestamp(block),
                canonical=True,
            )
            for block in blocks
        )
        session.add_all(
            RawEthereumLog(
                log_id=uuid.uuid4(),
                batch_id=batch_id,
                source_name=source_name,
                chain_id=chain_id,
                block_number=log.block_number,
                block_hash=log.block_hash,
                tx_hash=log.transaction_hash,
                transaction_index=log.transaction_index,
                log_index=log.log_index,
                contract_address=log.address,
                topics=list(log.topics),
                data=log.data,
                removed=log.removed,
                canonical=not log.removed,
            )
            for log in logs
        )
        protocol_stage(session, batch_id)

    return stage


async def ingest_ethereum_rpc(
    *,
    to_block: int,
    contract_address: str,
    from_block: int | None = None,
    rpc: EthereumRPC | None = None,
    engine: Engine | None = None,
    chain_config: EthereumChainConfig | None = None,
    quality_config: QualityConfig | None = None,
    failure_injector: FailureInjector | None = None,
) -> EthereumRPCIngestionSummary:
    """Ingest one explicit, finalized Ethereum block range through the shared pipeline."""
    config = chain_config or EthereumChainConfig.from_settings()
    engine = engine or create_database_engine()
    address = contract_address.lower()
    if not address.startswith("0x") or len(address) != 42:
        raise ValueError("Contract address must be a 20-byte 0x-prefixed address")
    if to_block < 0 or (from_block is not None and from_block < 0):
        raise ValueError("Block numbers cannot be negative")
    if config.max_block_range <= 0:
        raise ValueError("Maximum block range must be positive")
    rpc_client = rpc or HttpEthereumRPC(
        config.rpc_url,
        timeout_seconds=config.request_timeout_seconds,
        max_retries=config.max_retries,
    )
    connected_chain_id = await rpc_client.chain_id()
    if connected_chain_id != config.chain_id:
        raise ChainIDMismatchError(
            f"RPC chain ID {connected_chain_id} does not match configured chain ID "
            f"{config.chain_id}"
        )

    latest = await rpc_client.get_block_by_number("latest")
    finalized_boundary = latest.number - config.confirmation_depth
    effective_to = min(to_block, finalized_boundary)
    if effective_to < 0:
        raise FinalizedRangeError("The configured confirmation depth leaves no finalized blocks")

    source_name = _source_name(config.chain_id, address)
    checkpoint_before_model = _load_checkpoint(engine, source_name)
    checkpoint_before = (
        checkpoint_before_model.checkpoint_value if checkpoint_before_model is not None else None
    )
    reorganization = await _handle_reorganization(
        rpc_client,
        engine,
        checkpoint_before_model,
        config,
    )
    checkpoint_model = _load_checkpoint(engine, source_name)
    if from_block is None and checkpoint_model is None:
        raise FinalizedRangeError(
            "--from-block is required when no checkpoint exists for this chain and contract"
        )
    start = from_block if from_block is not None else checkpoint_model.block_number + 1
    if start > effective_to:
        raise FinalizedRangeError(
            f"Requested range starts at {start}, above finalized boundary {finalized_boundary}"
        )

    logs: list[EthereumLog] = []
    processed_chunks = 0
    for chunk_start, chunk_end in _chunk_ranges(start, effective_to, config.max_block_range):
        logs.extend(await rpc_client.get_logs(chunk_start, chunk_end, address))
        processed_chunks += 1

    blocks = tuple(
        [await rpc_client.get_block_by_number(number) for number in range(start, effective_to + 1)]
    )
    blocks_by_number = {block.number: block for block in blocks}
    for log in logs:
        block = blocks_by_number.get(log.block_number)
        if block is None or block.block_hash != log.block_hash:
            raise EthereumRPCIngestionError(
                f"Log {log.transaction_hash}:{log.log_index} does not match its block header"
            )

    endpoint = blocks_by_number[effective_to]
    source: dict[str, Any] = {
        "source_name": source_name,
        "rpc_mode": True,
        "chain_id": config.chain_id,
        "chain_name": config.chain_name,
        "contract_address": address,
        "rpc_logs": [_log_payload(log, blocks_by_number[log.block_number]) for log in logs],
        "observed_blocks": [_block_payload(block) for block in blocks],
        "checkpoint": _checkpoint_value(endpoint.number, endpoint.block_hash),
        "checkpoint_timestamp": _timestamp(endpoint).isoformat(),
        "checkpoint_chain_id": config.chain_id,
        "checkpoint_source_identity": address,
        "checkpoint_block_number": endpoint.number,
        "checkpoint_block_hash": endpoint.block_hash,
        "accounts": [],
        "assets": [],
        "transactions": [],
        "balances": [],
    }
    if checkpoint_model is not None:
        source["previous_checkpoint"] = checkpoint_model.checkpoint_value

    plugin: ProtocolPlugin | None = detect_rpc_protocol(source)
    protocol = (
        plugin.normalize(source)
        if plugin is not None
        else ProtocolNormalization(events=(), account_addresses=frozenset(), raw_records=())
    )
    analysis_status = (
        AnalysisStatus.PARTIALLY_SUPPORTED if plugin is not None else AnalysisStatus.UNSUPPORTED
    )
    normalized = _normalize_source(source, protocol, engine)
    source_content = json.dumps(
        {
            "chain_id": config.chain_id,
            "contract_address": address,
            "from_block": start,
            "to_block": effective_to,
            "rpc_logs": source["rpc_logs"],
            "observed_blocks": source["observed_blocks"],
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    pipeline = ingest_fixture_payload(
        normalized,
        source_content,
        engine,
        quality_config=quality_config,
        failure_injector=failure_injector,
        raw_stager=_rpc_raw_stager(source, protocol, blocks, tuple(logs)),
        stage_financial_records=False,
        protocol_plugin=plugin,
        protocol_source=source,
        analysis_status=analysis_status,
        origin=IncidentOrigin.LIVE,
        invariant_context=InvariantContext(
            source_system=source_name,
            chain_id=config.chain_id,
            block_range=(start, effective_to),
            evaluation_scope=EvaluationScope.PARTIAL_HISTORY,
        ),
    )
    checkpoint_after_model = _load_checkpoint(engine, source_name)
    return EthereumRPCIngestionSummary(
        pipeline=pipeline,
        chain_id=config.chain_id,
        chain_name=config.chain_name,
        contract_address=address,
        requested_from_block=from_block,
        requested_to_block=to_block,
        from_block=start,
        to_block=effective_to,
        finalized_boundary=finalized_boundary,
        range_truncated=effective_to != to_block,
        processed_chunks=processed_chunks,
        observed_logs=len(logs),
        normalized_events=len(protocol.events),
        checkpoint_before=checkpoint_before,
        checkpoint_after=(
            checkpoint_after_model.checkpoint_value if checkpoint_after_model is not None else None
        ),
        reorganization=reorganization,
    )
