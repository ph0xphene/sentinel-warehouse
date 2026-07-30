import asyncio
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Protocol

import httpx


class RPCError(RuntimeError):
    """Base class for typed Ethereum JSON-RPC failures."""


class RPCTransportError(RPCError):
    """The provider could not be reached after bounded retries."""


class RPCResponseError(RPCError):
    """The provider returned an invalid or explicit JSON-RPC error."""


@dataclass(frozen=True)
class EthereumBlock:
    number: int
    block_hash: str
    parent_hash: str
    timestamp: int


@dataclass(frozen=True)
class EthereumLog:
    address: str
    topics: tuple[str, ...]
    data: str
    block_number: int
    block_hash: str
    transaction_hash: str
    log_index: int
    removed: bool = False


class EthereumRPC(Protocol):
    async def chain_id(self) -> int:
        """Return the connected EVM chain ID."""

    async def get_block_by_number(self, block_number: int | str) -> EthereumBlock:
        """Return a block header by height or the `latest` tag."""

    async def get_logs(
        self,
        from_block: int,
        to_block: int,
        address: str,
    ) -> tuple[EthereumLog, ...]:
        """Return logs for one address in an inclusive finite range."""


def _quantity(value: object, field_name: str) -> int:
    if not isinstance(value, str) or not value.startswith("0x"):
        raise RPCResponseError(f"Invalid hexadecimal quantity for {field_name}")
    try:
        return int(value, 16)
    except ValueError as error:
        raise RPCResponseError(f"Invalid hexadecimal quantity for {field_name}") from error


def _block(value: object) -> EthereumBlock:
    if not isinstance(value, Mapping):
        raise RPCResponseError("eth_getBlockByNumber returned no block")
    return EthereumBlock(
        number=_quantity(value.get("number"), "block.number"),
        block_hash=str(value.get("hash", "")).lower(),
        parent_hash=str(value.get("parentHash", "")).lower(),
        timestamp=_quantity(value.get("timestamp"), "block.timestamp"),
    )


def _log(value: object) -> EthereumLog:
    if not isinstance(value, Mapping):
        raise RPCResponseError("eth_getLogs returned a non-object log")
    topics = value.get("topics")
    if not isinstance(topics, Sequence) or isinstance(topics, (str, bytes)):
        raise RPCResponseError("Ethereum log topics must be an array")
    return EthereumLog(
        address=str(value.get("address", "")).lower(),
        topics=tuple(str(topic).lower() for topic in topics),
        data=str(value.get("data", "0x")).lower(),
        block_number=_quantity(value.get("blockNumber"), "log.blockNumber"),
        block_hash=str(value.get("blockHash", "")).lower(),
        transaction_hash=str(value.get("transactionHash", "")).lower(),
        log_index=_quantity(value.get("logIndex"), "log.logIndex"),
        removed=bool(value.get("removed", False)),
    )


class HttpEthereumRPC:
    """Provider-neutral async JSON-RPC client with bounded transport retries."""

    def __init__(
        self,
        url: str,
        *,
        timeout_seconds: float = 10.0,
        max_retries: int = 3,
        client: httpx.AsyncClient | None = None,
        sleeper: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if not url:
            raise ValueError("Ethereum RPC URL is required")
        if max_retries < 0:
            raise ValueError("max_retries cannot be negative")
        self._url = url
        self._timeout = timeout_seconds
        self._max_retries = max_retries
        self._client = client
        self._sleeper = sleeper
        self._request_id = 0

    async def _request(self, method: str, params: list[object]) -> object:
        self._request_id += 1
        payload = {
            "jsonrpc": "2.0",
            "id": self._request_id,
            "method": method,
            "params": params,
        }
        owns_client = self._client is None
        client = self._client or httpx.AsyncClient(timeout=self._timeout)
        try:
            for attempt in range(self._max_retries + 1):
                try:
                    response = await client.post(self._url, json=payload, timeout=self._timeout)
                    response.raise_for_status()
                    body = response.json()
                    if not isinstance(body, Mapping):
                        raise RPCResponseError(f"{method} returned a non-object response")
                    if body.get("error") is not None:
                        raise RPCResponseError(f"{method} failed: {body['error']}")
                    if "result" not in body:
                        raise RPCResponseError(f"{method} response omitted result")
                    return body["result"]
                except RPCResponseError:
                    raise
                except (httpx.HTTPError, ValueError) as error:
                    if attempt == self._max_retries:
                        raise RPCTransportError(
                            f"{method} failed after {attempt + 1} attempts"
                        ) from error
                    await self._sleeper(0.25 * (2**attempt))
        finally:
            if owns_client:
                await client.aclose()
        raise AssertionError("unreachable")

    async def chain_id(self) -> int:
        return _quantity(await self._request("eth_chainId", []), "chainId")

    async def get_block_by_number(self, block_number: int | str) -> EthereumBlock:
        tag = block_number if isinstance(block_number, str) else hex(block_number)
        return _block(await self._request("eth_getBlockByNumber", [tag, False]))

    async def get_logs(
        self,
        from_block: int,
        to_block: int,
        address: str,
    ) -> tuple[EthereumLog, ...]:
        result = await self._request(
            "eth_getLogs",
            [
                {
                    "fromBlock": hex(from_block),
                    "toBlock": hex(to_block),
                    "address": address.lower(),
                }
            ],
        )
        if not isinstance(result, list):
            raise RPCResponseError("eth_getLogs returned a non-array result")
        return tuple(_log(item) for item in result)


@dataclass
class FakeEthereumRPC:
    """Deterministic in-memory RPC implementation for network-free tests."""

    chain: int
    blocks: dict[int, EthereumBlock]
    logs: tuple[EthereumLog, ...] = ()
    latest_block: int | None = None
    calls: list[tuple[str, object]] = field(default_factory=list)

    async def chain_id(self) -> int:
        self.calls.append(("eth_chainId", None))
        return self.chain

    async def get_block_by_number(self, block_number: int | str) -> EthereumBlock:
        self.calls.append(("eth_getBlockByNumber", block_number))
        number = (
            self.latest_block
            if block_number == "latest" and self.latest_block is not None
            else block_number
        )
        if number == "latest":
            number = max(self.blocks)
        try:
            return self.blocks[int(number)]
        except (KeyError, ValueError) as error:
            raise RPCResponseError(f"Fake RPC has no block {block_number}") from error

    async def get_logs(
        self,
        from_block: int,
        to_block: int,
        address: str,
    ) -> tuple[EthereumLog, ...]:
        self.calls.append(("eth_getLogs", (from_block, to_block, address.lower())))
        return tuple(
            log
            for log in self.logs
            if from_block <= log.block_number <= to_block
            and log.address == address.lower()
            and not log.removed
        )
