import asyncio

import httpx
import pytest

from sentinel.ethereum import (
    EthereumBlock,
    EthereumLog,
    FakeEthereumRPC,
    HttpEthereumRPC,
)
from sentinel.ingestion.ethereum_rpc import (
    ChainIDMismatchError,
    EthereumChainConfig,
    _chunk_ranges,
    ingest_ethereum_rpc,
)

ADDRESS = "0x1111111111111111111111111111111111111111"


def _hash(number: int) -> str:
    return f"0x{number:064x}"


def test_rpc_range_chunking_and_unknown_log_preservation() -> None:
    blocks = {
        number: EthereumBlock(
            number=number,
            block_hash=_hash(number),
            parent_hash=_hash(number - 1),
            timestamp=1_700_000_000 + number,
        )
        for number in range(10, 16)
    }
    unknown = EthereumLog(
        address=ADDRESS,
        topics=("0x" + "ff" * 32,),
        data="0x",
        block_number=12,
        block_hash=_hash(12),
        transaction_hash="0x" + "aa" * 32,
        log_index=0,
    )
    rpc = FakeEthereumRPC(chain=1, blocks=blocks, logs=(unknown,), latest_block=15)

    async def collect() -> tuple[EthereumLog, ...]:
        observed: list[EthereumLog] = []
        for start, end in _chunk_ranges(10, 15, 2):
            observed.extend(await rpc.get_logs(start, end, ADDRESS))
        return tuple(observed)

    assert _chunk_ranges(10, 15, 2) == ((10, 11), (12, 13), (14, 15))
    assert asyncio.run(collect()) == (unknown,)
    assert [call[1] for call in rpc.calls] == [
        (10, 11, ADDRESS),
        (12, 13, ADDRESS),
        (14, 15, ADDRESS),
    ]


def test_http_rpc_retries_transient_transport_failure() -> None:
    attempts = 0
    backoffs: list[float] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(503, request=request)
        return httpx.Response(
            200,
            request=request,
            json={"jsonrpc": "2.0", "id": 1, "result": "0x1"},
        )

    async def sleeper(delay: float) -> None:
        backoffs.append(delay)

    async def execute() -> int:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            rpc = HttpEthereumRPC(
                "https://rpc.invalid",
                max_retries=2,
                client=client,
                sleeper=sleeper,
            )
            return await rpc.chain_id()

    assert asyncio.run(execute()) == 1
    assert attempts == 2
    assert backoffs == [0.25]


def test_wrong_rpc_chain_id_is_rejected_before_database_access() -> None:
    config = EthereumChainConfig(
        chain_id=1,
        chain_name="mainnet",
        rpc_url="",
        request_timeout_seconds=1,
        max_retries=0,
        confirmation_depth=0,
        max_block_range=10,
        reorg_lookback=5,
    )
    rpc = FakeEthereumRPC(chain=5, blocks={})

    with pytest.raises(ChainIDMismatchError, match="does not match"):
        asyncio.run(
            ingest_ethereum_rpc(
                from_block=1,
                to_block=1,
                contract_address=ADDRESS,
                rpc=rpc,
                engine=object(),  # type: ignore[arg-type]
                chain_config=config,
            )
        )
