import asyncio

import pytest
from sqlalchemy import func, select

from sentinel.ethereum import EthereumBlock, EthereumLog, FakeEthereumRPC
from sentinel.ingestion import (
    DeepReorganizationError,
    EthereumChainConfig,
    ingest_ethereum_rpc,
)
from sentinel.ingestion.failures import FailureInjector, FailurePoint
from sentinel.models import (
    FinancialEvent,
    Incident,
    IncidentStatus,
    IngestionStatus,
    RawEthereumBlock,
    RawEthereumLog,
    RawEthereumTransfer,
    SourceCheckpoint,
)
from sentinel.protocols.erc20 import TRANSFER_TOPIC
from sentinel.protocols.uniswap_v2 import SWAP_TOPIC, SYNC_TOPIC

pytestmark = pytest.mark.integration

CONTRACT = "0x1111111111111111111111111111111111111111"
ALICE = "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
BOB = "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"


def _hash(number: int, generation: int = 0) -> str:
    return f"0x{generation * 10000 + number:064x}"


def _blocks(start: int = 95, end: int = 110) -> dict[int, EthereumBlock]:
    return {
        number: EthereumBlock(
            number=number,
            block_hash=_hash(number),
            parent_hash=_hash(number - 1),
            timestamp=1_700_000_000 + number,
        )
        for number in range(start, end + 1)
    }


def _word(value: int) -> str:
    return f"{value:064x}"


def _topic_address(address: str) -> str:
    return f"0x{'0' * 24}{address[2:]}"


def _transfer_log(
    block_number: int,
    amount: int,
    *,
    tx_byte: str = "01",
    block_hash: str | None = None,
) -> EthereumLog:
    return EthereumLog(
        address=CONTRACT,
        topics=(TRANSFER_TOPIC, _topic_address(ALICE), _topic_address(BOB)),
        data=f"0x{_word(amount)}",
        block_number=block_number,
        block_hash=block_hash or _hash(block_number),
        transaction_hash=f"0x{tx_byte * 32}",
        log_index=0,
    )


def _uniswap_log(
    topic: str,
    block_number: int,
    values: tuple[int, ...],
    *,
    tx_byte: str,
) -> EthereumLog:
    topics = (
        (topic,) if topic == SYNC_TOPIC else (topic, _topic_address(ALICE), _topic_address(BOB))
    )
    return EthereumLog(
        address=CONTRACT,
        topics=topics,
        data="0x" + "".join(_word(value) for value in values),
        block_number=block_number,
        block_hash=_hash(block_number),
        transaction_hash=f"0x{tx_byte * 32}",
        log_index=0,
    )


def _config(
    *,
    confirmation_depth: int = 0,
    maximum_range: int = 2,
    lookback: int = 4,
) -> EthereumChainConfig:
    return EthereumChainConfig(
        chain_id=1,
        chain_name="mainnet",
        rpc_url="",
        request_timeout_seconds=1,
        max_retries=0,
        confirmation_depth=confirmation_depth,
        max_block_range=maximum_range,
        reorg_lookback=lookback,
    )


def _run(clean_engine, rpc, *, from_block, to_block, config=None, injector=None):
    return asyncio.run(
        ingest_ethereum_rpc(
            from_block=from_block,
            to_block=to_block,
            contract_address=CONTRACT,
            rpc=rpc,
            engine=clean_engine,
            chain_config=config or _config(),
            failure_injector=injector,
        )
    )


def test_successful_finalized_range_is_chunked_and_attributed(clean_engine) -> None:
    rpc = FakeEthereumRPC(
        chain=1,
        blocks=_blocks(),
        logs=(_transfer_log(101, 25),),
        latest_block=110,
    )

    summary = _run(
        clean_engine,
        rpc,
        from_block=100,
        to_block=105,
        config=_config(confirmation_depth=7),
    )

    with clean_engine.connect() as connection:
        block_count = connection.scalar(select(func.count()).select_from(RawEthereumBlock))
        log_count = connection.scalar(select(func.count()).select_from(RawEthereumLog))
        transfer = connection.execute(
            select(
                RawEthereumTransfer.chain_id,
                RawEthereumTransfer.block_number,
                RawEthereumTransfer.block_hash,
            )
        ).one()
        event = connection.execute(
            select(
                FinancialEvent.chain_id,
                FinancialEvent.block_number,
                FinancialEvent.block_hash,
                FinancialEvent.canonical,
            )
        ).one()

    assert summary.pipeline.status is IngestionStatus.SUCCEEDED
    assert summary.to_block == 103
    assert summary.finalized_boundary == 103
    assert summary.range_truncated
    assert summary.processed_chunks == 2
    assert summary.observed_logs == 1
    assert summary.normalized_events == 1
    assert block_count == 4
    assert log_count == 1
    assert transfer == (1, 101, _hash(101))
    assert event == (1, 101, _hash(101), True)
    assert summary.checkpoint_after == f"103:{_hash(103)}"


def test_failed_batch_does_not_advance_and_retry_reuses_raw_observations(clean_engine) -> None:
    rpc = FakeEthereumRPC(
        chain=1,
        blocks=_blocks(),
        logs=(_transfer_log(100, 10),),
        latest_block=110,
    )
    injector = FailureInjector(frozenset({FailurePoint.AFTER_RAW_STAGE}))

    failed = _run(
        clean_engine,
        rpc,
        from_block=100,
        to_block=101,
        injector=injector,
    )
    retried = _run(clean_engine, rpc, from_block=100, to_block=101)

    with clean_engine.connect() as connection:
        checkpoint_count = connection.scalar(select(func.count()).select_from(SourceCheckpoint))
        raw_blocks = connection.scalar(select(func.count()).select_from(RawEthereumBlock))

    assert failed.pipeline.status is IngestionStatus.FAILED
    assert failed.checkpoint_after is None
    assert retried.pipeline.status is IngestionStatus.SUCCEEDED
    assert retried.pipeline.batch_id == failed.pipeline.batch_id
    assert retried.pipeline.attempt_number == 2
    assert checkpoint_count == 1
    assert raw_blocks == 2


def test_checkpoint_resume_and_duplicate_range_are_idempotent(clean_engine) -> None:
    rpc = FakeEthereumRPC(
        chain=1,
        blocks=_blocks(),
        logs=(_transfer_log(100, 10), _transfer_log(102, 5, tx_byte="02")),
        latest_block=110,
    )

    first = _run(clean_engine, rpc, from_block=100, to_block=101)
    duplicate = _run(clean_engine, rpc, from_block=100, to_block=101)
    resumed = _run(clean_engine, rpc, from_block=None, to_block=103)

    with clean_engine.connect() as connection:
        event_count = connection.scalar(
            select(func.count())
            .select_from(FinancialEvent)
            .where(FinancialEvent.canonical.is_(True))
        )
        checkpoint = connection.execute(
            select(SourceCheckpoint.block_number, SourceCheckpoint.block_hash)
        ).one()

    assert duplicate.pipeline.idempotent
    assert duplicate.pipeline.batch_id == first.pipeline.batch_id
    assert resumed.from_block == 102
    assert resumed.pipeline.status is IngestionStatus.SUCCEEDED
    assert event_count == 2
    assert checkpoint == (103, _hash(103))


def test_unknown_log_is_preserved_without_normalization(clean_engine) -> None:
    unknown = EthereumLog(
        address=CONTRACT,
        topics=("0x" + "ff" * 32,),
        data="0x1234",
        block_number=100,
        block_hash=_hash(100),
        transaction_hash="0x" + "99" * 32,
        log_index=4,
    )
    rpc = FakeEthereumRPC(chain=1, blocks=_blocks(), logs=(unknown,), latest_block=110)

    summary = _run(clean_engine, rpc, from_block=100, to_block=100)

    with clean_engine.connect() as connection:
        raw = connection.execute(
            select(RawEthereumLog.topics, RawEthereumLog.data, RawEthereumLog.canonical)
        ).one()
        events = connection.scalar(select(func.count()).select_from(FinancialEvent))

    assert summary.pipeline.status is IngestionStatus.SUCCEEDED
    assert summary.observed_logs == 1
    assert summary.normalized_events == 0
    assert raw == ([unknown.topics[0]], "0x1234", True)
    assert events == 0


def test_shallow_reorg_rewinds_replays_and_preserves_orphaned_evidence(clean_engine) -> None:
    blocks = _blocks()
    old_log = _transfer_log(101, 10)
    rpc = FakeEthereumRPC(chain=1, blocks=blocks, logs=(old_log,), latest_block=110)
    first = _run(
        clean_engine,
        rpc,
        from_block=100,
        to_block=102,
        config=_config(lookback=2),
    )

    blocks[101] = EthereumBlock(101, _hash(101, 1), _hash(100), 1_700_000_101)
    blocks[102] = EthereumBlock(102, _hash(102, 1), _hash(101, 1), 1_700_000_102)
    new_log = _transfer_log(101, 12, tx_byte="02", block_hash=_hash(101, 1))
    rpc.logs = (new_log,)
    replay = _run(
        clean_engine,
        rpc,
        from_block=None,
        to_block=102,
        config=_config(lookback=2),
    )

    with clean_engine.connect() as connection:
        canonical_events = connection.scalar(
            select(func.count())
            .select_from(FinancialEvent)
            .where(FinancialEvent.canonical.is_(True))
        )
        orphaned_events = connection.scalar(
            select(func.count())
            .select_from(FinancialEvent)
            .where(FinancialEvent.canonical.is_(False))
        )
        orphaned_logs = connection.scalar(
            select(func.count())
            .select_from(RawEthereumLog)
            .where(RawEthereumLog.canonical.is_(False))
        )
        checkpoint_hash = connection.scalar(select(SourceCheckpoint.block_hash))

    assert first.pipeline.status is IngestionStatus.SUCCEEDED
    assert replay.pipeline.status is IngestionStatus.SUCCEEDED
    assert replay.reorganization is not None
    assert replay.reorganization.common_ancestor == 100
    assert replay.reorganization.orphaned_block_count == 2
    assert replay.from_block == 101
    assert canonical_events == 1
    assert orphaned_events == 1
    assert orphaned_logs == 1
    assert checkpoint_hash == _hash(102, 1)


def test_deep_reorg_creates_operational_incident(clean_engine) -> None:
    blocks = _blocks()
    rpc = FakeEthereumRPC(
        chain=1,
        blocks=blocks,
        logs=(_transfer_log(101, 10),),
        latest_block=110,
    )
    _run(clean_engine, rpc, from_block=100, to_block=102, config=_config(lookback=1))
    blocks[101] = EthereumBlock(101, _hash(101, 2), _hash(100, 2), 1_700_000_101)
    blocks[102] = EthereumBlock(102, _hash(102, 2), _hash(101, 2), 1_700_000_102)

    with pytest.raises(DeepReorganizationError):
        _run(clean_engine, rpc, from_block=None, to_block=103, config=_config(lookback=1))

    with clean_engine.connect() as connection:
        incident = connection.execute(
            select(Incident.incident_type, Incident.severity, Incident.status)
        ).one()
        checkpoint_number = connection.scalar(select(SourceCheckpoint.block_number))

    assert incident.incident_type == "ethereum_deep_reorganization"
    assert incident.severity == "critical"
    assert incident.status is IncidentStatus.OPEN
    assert checkpoint_number == 102


def test_protocol_invariant_failure_uses_existing_incident_system(clean_engine) -> None:
    logs = (
        _uniswap_log(SYNC_TOPIC, 100, (100, 100), tx_byte="10"),
        _uniswap_log(SWAP_TOPIC, 101, (10, 0, 0, 5), tx_byte="11"),
        _uniswap_log(SYNC_TOPIC, 102, (109, 95), tx_byte="12"),
    )
    rpc = FakeEthereumRPC(chain=1, blocks=_blocks(), logs=logs, latest_block=110)

    summary = _run(clean_engine, rpc, from_block=100, to_block=102)

    with clean_engine.connect() as connection:
        incident = connection.execute(
            select(Incident.incident_type, Incident.protocol_name, Incident.status)
        ).one()
        checkpoint_count = connection.scalar(select(func.count()).select_from(SourceCheckpoint))

    assert summary.pipeline.status is IngestionStatus.FAILED
    assert incident.incident_type == "reserve_consistency"
    assert incident.protocol_name == "uniswap_v2"
    assert incident.status is IncidentStatus.OPEN
    assert checkpoint_count == 0
