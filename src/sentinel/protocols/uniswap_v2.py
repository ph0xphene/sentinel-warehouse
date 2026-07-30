from collections.abc import Mapping, Sequence
from decimal import Decimal
from typing import Any

from sentinel.ethereum.abi import decode_topic_address, decode_words
from sentinel.protocols.base import ProtocolNormalization, ProtocolRawRecord
from sentinel.security import (
    CanonicalEvent,
    InvariantContext,
    InvariantExecutionResult,
    InvariantOutcome,
)

MINT_TOPIC = "0x4c209b5fc8ad50758f13e2e1088ba56a560dff690a1c6fef26394f4c03821c4f"
BURN_TOPIC = "0xdccd412f0b1252819cb1fd330b93224ca42612892bb3f4f789976e6d81936496"
SWAP_TOPIC = "0xd78ad95fa46c994b6551d0da85fc275fe613ce37657fb8d5e3d130840159d822"
SYNC_TOPIC = "0x1c411e9a96e071241c2f21f7726b17ae89e3cab4c78be50e062b03a9fffbbad1"
UNISWAP_TOPICS = {MINT_TOPIC, BURN_TOPIC, SWAP_TOPIC, SYNC_TOPIC}


def _address(value: object) -> str:
    return str(value).lower()


def _amount(value: object, decimals: int) -> str:
    return format(Decimal(str(value)) / (Decimal(10) ** decimals), "f")


def _topics(log: Mapping[str, Any]) -> Sequence[object]:
    topics = log.get("topics")
    if not isinstance(topics, Sequence) or isinstance(topics, (str, bytes)):
        raise ValueError("Uniswap log topics must be an array")
    return topics


def _rpc_source(source: Mapping[str, Any]) -> Mapping[str, Any]:
    if source.get("rpc_mode") is not True:
        return source

    contract = _address(source["contract_address"])
    protocol_events: list[dict[str, Any]] = []
    transactions: list[dict[str, Any]] = []
    for log in source.get("rpc_logs", []):
        if not isinstance(log, Mapping) or not log.get("topics"):
            continue
        topic = str(log["topics"][0]).lower()
        if topic not in UNISWAP_TOPICS:
            continue
        topics = _topics(log)
        event: dict[str, Any] = {
            "tx_hash": str(log["transaction_hash"]).lower(),
            "log_index": int(log["log_index"]),
            "transaction_index": int(log.get("transaction_index", 0)),
            "chain_id": int(source["chain_id"]),
            "block_number": int(log["block_number"]),
            "block_hash": str(log["block_hash"]).lower(),
        }
        if topic == MINT_TOPIC:
            amount0, amount1 = decode_words(log["data"], 2)
            event.update(
                event_name="Mint",
                sender=decode_topic_address(topics[1]),
                amount0=amount0,
                amount1=amount1,
            )
        elif topic == BURN_TOPIC:
            amount0, amount1 = decode_words(log["data"], 2)
            event.update(
                event_name="Burn",
                sender=decode_topic_address(topics[1]),
                to=decode_topic_address(topics[2]),
                amount0=amount0,
                amount1=amount1,
            )
        elif topic == SWAP_TOPIC:
            amount0_in, amount1_in, amount0_out, amount1_out = decode_words(log["data"], 4)
            event.update(
                event_name="Swap",
                sender=decode_topic_address(topics[1]),
                to=decode_topic_address(topics[2]),
                amount0_in=amount0_in,
                amount1_in=amount1_in,
                amount0_out=amount0_out,
                amount1_out=amount1_out,
            )
        else:
            reserve0, reserve1 = decode_words(log["data"], 2)
            event.update(event_name="Sync", reserve0=reserve0, reserve1=reserve1)
        protocol_events.append(event)
        transactions.append(
            {
                "tx_hash": event["tx_hash"],
                "block_number": event["block_number"],
                "transaction_index": event["transaction_index"],
                "block_hash": event["block_hash"],
                "block_timestamp": log["block_timestamp"],
                "success": True,
            }
        )

    return {
        **source,
        "protocol": "uniswap_v2",
        "pair": {"address": contract, "token0": None, "token1": None},
        "tokens": [],
        "protocol_events": protocol_events,
        "transactions": transactions,
    }


def _transaction_map(source: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    transactions: dict[str, dict[str, Any]] = {}
    for position, transaction in enumerate(source.get("transactions", [])):
        if not isinstance(transaction, Mapping):
            continue
        value = dict(transaction)
        value.setdefault("transaction_index", position)
        transactions[str(transaction["tx_hash"]).lower()] = value
    return transactions


def _chain_coordinates(
    event: Mapping[str, Any],
    transactions: Mapping[str, Mapping[str, Any]],
) -> tuple[int, int, int]:
    transaction = transactions[str(event["tx_hash"]).lower()]
    return (
        int(event.get("block_number", transaction["block_number"])),
        int(event.get("transaction_index", transaction.get("transaction_index", 0))),
        int(event["log_index"]),
    )


def _ordered_protocol_events(
    source: Mapping[str, Any],
    transactions: Mapping[str, Mapping[str, Any]],
) -> tuple[Mapping[str, Any], ...]:
    values = tuple(
        event for event in source.get("protocol_events", []) if isinstance(event, Mapping)
    )
    return tuple(sorted(values, key=lambda event: _chain_coordinates(event, transactions)))


def _opening_reserves(
    source: Mapping[str, Any],
    pair_address: str,
    token0: str | None,
    token1: str | None,
) -> tuple[Decimal, Decimal] | None:
    if token0 is None or token1 is None:
        return None
    values = {
        (_address(balance["address"]), _address(balance["token_address"])): Decimal(
            str(balance["amount"])
        )
        for balance in source.get("opening_balances", [])
        if isinstance(balance, Mapping)
    }
    key0 = (pair_address, token0)
    key1 = (pair_address, token1)
    if key0 not in values or key1 not in values:
        return None
    return values[key0], values[key1]


def _result(
    *,
    name: str,
    severity: str,
    description: str,
    affected: list[dict[str, object]],
    insufficient: list[dict[str, object]],
) -> InvariantOutcome:
    if affected:
        return InvariantOutcome(
            name=name,
            severity=severity,
            description=description,
            affected_records=tuple(affected),
            protocol_name="uniswap_v2",
        )
    if insufficient:
        return InvariantOutcome(
            name=name,
            severity=severity,
            description=description,
            affected_records=tuple(insufficient),
            protocol_name="uniswap_v2",
            result=InvariantExecutionResult.INSUFFICIENT_EVIDENCE,
        )
    return InvariantOutcome(
        name=name,
        severity=severity,
        description=description,
        affected_records=(),
        protocol_name="uniswap_v2",
    )


class UniswapV2Plugin:
    name = "uniswap_v2"

    def detect(self, source: Mapping[str, Any]) -> bool:
        if str(source.get("protocol", "")).lower() == self.name:
            return True
        return any(
            isinstance(log, Mapping)
            and bool(log.get("topics"))
            and str(log["topics"][0]).lower() in UNISWAP_TOPICS
            for log in source.get("rpc_logs", [])
        )

    def normalize(self, source: Mapping[str, Any]) -> ProtocolNormalization:
        source_values = _rpc_source(source)
        pair = source_values["pair"]
        pair_address = _address(pair["address"])
        token0 = _address(pair["token0"]) if pair.get("token0") is not None else None
        token1 = _address(pair["token1"]) if pair.get("token1") is not None else None
        tokens = {
            _address(token["address"]): token
            for token in source_values.get("tokens", [])
            if isinstance(token, Mapping)
        }
        known_assets = token0 in tokens and token1 in tokens
        decimals0 = int(tokens[token0]["decimals"]) if known_assets and token0 is not None else None
        decimals1 = int(tokens[token1]["decimals"]) if known_assets and token1 is not None else None
        transactions = _transaction_map(source_values)
        protocol_events = _ordered_protocol_events(source_values, transactions)
        events: list[dict[str, Any]] = []
        addresses = {pair_address}
        raw_records: list[ProtocolRawRecord] = []
        tracked_reserves = _opening_reserves(source_values, pair_address, token0, token1)

        def add_event(
            source_event: Mapping[str, Any],
            suffix: str,
            event_type: str,
            amount: object,
            *,
            asset: str | None,
            account_from: str | None = None,
            account_to: str | None = None,
            extra_metadata: Mapping[str, object] | None = None,
        ) -> None:
            tx_hash = str(source_event["tx_hash"]).lower()
            block_number, transaction_index, log_index = _chain_coordinates(
                source_event, transactions
            )
            transaction = transactions[tx_hash]
            metadata: dict[str, object] = {
                "protocol": self.name,
                "protocol_event": source_event["event_name"],
                "tx_hash": tx_hash,
                "asset_metadata_status": "known" if known_assets else "unknown",
                **(dict(extra_metadata) if extra_metadata is not None else {}),
            }
            if source_event.get("chain_id") is not None:
                metadata["chain_id"] = int(source_event["chain_id"])
            if source_event.get("block_hash") is not None:
                metadata["block_hash"] = str(source_event["block_hash"]).lower()
            events.append(
                {
                    "external_id": f"{tx_hash}:{log_index}:{suffix}",
                    "event_type": event_type,
                    "occurred_at": transaction["block_timestamp"],
                    "asset_external_id": asset,
                    "account_from_external_id": account_from,
                    "account_to_external_id": account_to,
                    "amount": amount,
                    "chain_id": (
                        int(source_event["chain_id"])
                        if source_event.get("chain_id") is not None
                        else None
                    ),
                    "block_number": block_number,
                    "block_hash": (
                        str(source_event["block_hash"]).lower()
                        if source_event.get("block_hash") is not None
                        else None
                    ),
                    "transaction_index": transaction_index,
                    "log_index": log_index,
                    "metadata": metadata,
                }
            )

        for source_event in protocol_events:
            event_name = str(source_event["event_name"])
            tx_hash = str(source_event["tx_hash"]).lower()
            _, _, log_index = _chain_coordinates(source_event, transactions)
            raw_records.append(
                ProtocolRawRecord(
                    record_type=f"ethereum.uniswap_v2.{event_name.lower()}",
                    external_id=f"{tx_hash}:{log_index}",
                    payload=dict(source_event),
                )
            )
            for address_field in ("sender", "to"):
                if source_event.get(address_field) is not None:
                    addresses.add(_address(source_event[address_field]))

            if event_name == "Sync":
                current = (
                    Decimal(str(source_event["reserve0"])),
                    Decimal(str(source_event["reserve1"])),
                )
                if (
                    known_assets
                    and token0 is not None
                    and token1 is not None
                    and decimals0 is not None
                    and decimals1 is not None
                ):
                    for slot, token, decimals in (
                        (0, token0, decimals0),
                        (1, token1, decimals1),
                    ):
                        previous = tracked_reserves[slot] if tracked_reserves is not None else None
                        delta = current[slot] - previous if previous is not None else Decimal(0)
                        if delta > 0:
                            add_event(
                                source_event,
                                f"sync:token{slot}",
                                "DEPOSIT",
                                _amount(delta, decimals),
                                asset=token,
                                account_to=pair_address,
                                extra_metadata={"reserve": str(current[slot])},
                            )
                        elif delta < 0:
                            add_event(
                                source_event,
                                f"sync:token{slot}",
                                "WITHDRAWAL",
                                _amount(abs(delta), decimals),
                                asset=token,
                                account_from=pair_address,
                                extra_metadata={"reserve": str(current[slot])},
                            )
                        else:
                            add_event(
                                source_event,
                                f"sync:token{slot}",
                                "ADJUSTMENT",
                                "0",
                                asset=token,
                                account_from=pair_address,
                                account_to=pair_address,
                                extra_metadata={"reserve": str(current[slot])},
                            )
                else:
                    add_event(
                        source_event,
                        "sync:unknown-assets",
                        "ADJUSTMENT",
                        "0",
                        asset=None,
                        extra_metadata={
                            "reserve0": str(current[0]),
                            "reserve1": str(current[1]),
                        },
                    )
                tracked_reserves = current
            elif event_name in {"Mint", "Burn", "Swap"}:
                add_event(
                    source_event,
                    event_name.lower(),
                    "ADJUSTMENT",
                    "0",
                    asset=None,
                    extra_metadata={
                        key: str(value)
                        for key, value in source_event.items()
                        if key.startswith("amount")
                    },
                )
            else:
                raise ValueError(f"Unsupported Uniswap V2 event: {event_name}")

        return ProtocolNormalization(
            events=tuple(events),
            account_addresses=frozenset(addresses),
            raw_records=tuple(raw_records),
            asset_definitions=tuple(dict(token) for token in tokens.values()),
        )

    def invariants(
        self,
        events: tuple[CanonicalEvent, ...],
        source: Mapping[str, Any],
        context: InvariantContext,
    ) -> tuple[InvariantOutcome, ...]:
        del events, context
        source_values = _rpc_source(source)
        pair = source_values["pair"]
        pair_address = _address(pair["address"])
        token0 = _address(pair["token0"]) if pair.get("token0") is not None else None
        token1 = _address(pair["token1"]) if pair.get("token1") is not None else None
        transactions = _transaction_map(source_values)
        protocol_events = _ordered_protocol_events(source_values, transactions)
        baseline = _opening_reserves(source_values, pair_address, token0, token1)
        pending_sync: tuple[Mapping[str, Any], tuple[Decimal, Decimal]] | None = None
        reserve_affected: list[dict[str, object]] = []
        reserve_insufficient: list[dict[str, object]] = []
        liquidity_affected: list[dict[str, object]] = []
        liquidity_insufficient: list[dict[str, object]] = []

        def event_id(event: Mapping[str, Any]) -> str:
            return f"{event['tx_hash']}:{event['log_index']}"

        def settle_standalone_sync() -> None:
            nonlocal baseline, pending_sync
            if pending_sync is None:
                return
            sync_event, current = pending_sync
            if baseline is not None and (current[0] < baseline[0] or current[1] < baseline[1]):
                reserve_affected.append(
                    {
                        "external_id": event_id(sync_event),
                        "pair_address": pair_address,
                        "reason": "standalone Sync decreased reserves without Burn or Swap",
                        "previous_reserve0": str(baseline[0]),
                        "previous_reserve1": str(baseline[1]),
                        "actual_reserve0": str(current[0]),
                        "actual_reserve1": str(current[1]),
                    }
                )
            baseline = current
            pending_sync = None

        for source_event in protocol_events:
            name = str(source_event["event_name"])
            if name == "Sync":
                settle_standalone_sync()
                pending_sync = (
                    source_event,
                    (
                        Decimal(str(source_event["reserve0"])),
                        Decimal(str(source_event["reserve1"])),
                    ),
                )
                continue

            if name not in {"Mint", "Burn", "Swap"}:
                continue
            if (
                pending_sync is None
                or str(pending_sync[0]["tx_hash"]).lower() != str(source_event["tx_hash"]).lower()
            ):
                settle_standalone_sync()
                reserve_insufficient.append(
                    {
                        "external_id": event_id(source_event),
                        "reason": f"{name} has no preceding Sync in the same transaction",
                    }
                )
                if name == "Swap":
                    liquidity_insufficient.append(
                        {
                            "external_id": event_id(source_event),
                            "reason": "swap product cannot be evaluated without its preceding Sync",
                        }
                    )
                continue

            sync_event, current = pending_sync
            if baseline is None:
                reserve_insufficient.append(
                    {
                        "external_id": event_id(sync_event),
                        "reason": "first observed Sync has no known prior reserves",
                    }
                )
                if name == "Swap":
                    liquidity_insufficient.append(
                        {
                            "external_id": event_id(source_event),
                            "reason": "swap product cannot be evaluated without prior reserves",
                        }
                    )
            else:
                if name == "Mint":
                    expected = (
                        baseline[0] + Decimal(str(source_event["amount0"])),
                        baseline[1] + Decimal(str(source_event["amount1"])),
                    )
                elif name == "Burn":
                    expected = (
                        baseline[0] - Decimal(str(source_event["amount0"])),
                        baseline[1] - Decimal(str(source_event["amount1"])),
                    )
                else:
                    expected = (
                        baseline[0]
                        + Decimal(str(source_event.get("amount0_in", 0)))
                        - Decimal(str(source_event.get("amount0_out", 0))),
                        baseline[1]
                        + Decimal(str(source_event.get("amount1_in", 0)))
                        - Decimal(str(source_event.get("amount1_out", 0))),
                    )
                if current != expected:
                    reserve_affected.append(
                        {
                            "external_id": event_id(source_event),
                            "sync_external_id": event_id(sync_event),
                            "pair_address": pair_address,
                            "expected_reserve0": str(expected[0]),
                            "actual_reserve0": str(current[0]),
                            "expected_reserve1": str(expected[1]),
                            "actual_reserve1": str(current[1]),
                        }
                    )
                if name == "Swap":
                    before_product = baseline[0] * baseline[1]
                    after_product = current[0] * current[1]
                    if after_product < before_product:
                        liquidity_affected.append(
                            {
                                "external_id": event_id(source_event),
                                "pair_address": pair_address,
                                "product_before": str(before_product),
                                "product_after": str(after_product),
                            }
                        )
            baseline = current
            pending_sync = None

        settle_standalone_sync()
        return (
            _result(
                name="reserve_consistency",
                severity="critical",
                description=(
                    "Uniswap action amounts reconcile the preceding Sync reserve update; "
                    "standalone Sync increases are treated as donations."
                ),
                affected=reserve_affected,
                insufficient=reserve_insufficient,
            ),
            _result(
                name="liquidity_conservation",
                severity="high",
                description="Uniswap swap reserve product does not decrease.",
                affected=liquidity_affected,
                insufficient=liquidity_insufficient,
            ),
        )
