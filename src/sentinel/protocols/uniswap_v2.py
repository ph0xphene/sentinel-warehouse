from collections.abc import Mapping, Sequence
from decimal import Decimal
from typing import Any

from sentinel.ethereum.abi import decode_topic_address, decode_words
from sentinel.protocols.base import ProtocolNormalization, ProtocolRawRecord
from sentinel.security import (
    CanonicalEvent,
    InvariantOutcome,
    reconstruct_balances,
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
    token0 = f"{contract}:token0"
    token1 = f"{contract}:token1"
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
                "block_hash": event["block_hash"],
                "block_timestamp": log["block_timestamp"],
                "success": True,
            }
        )

    return {
        **source,
        "protocol": "uniswap_v2",
        "pair": {"address": contract, "token0": token0, "token1": token1},
        "tokens": [
            {
                "address": token0,
                "symbol": "TOKEN0",
                "name": f"Uniswap reserve 0 {contract[:10]}",
                "decimals": 0,
            },
            {
                "address": token1,
                "symbol": "TOKEN1",
                "name": f"Uniswap reserve 1 {contract[:10]}",
                "decimals": 0,
            },
        ],
        "protocol_events": protocol_events,
        "transactions": transactions,
    }


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
        token0 = _address(pair["token0"])
        token1 = _address(pair["token1"])
        tokens = {
            _address(token["address"]): token
            for token in source_values.get("tokens", [])
            if isinstance(token, Mapping)
        }
        decimals0 = int(tokens[token0]["decimals"])
        decimals1 = int(tokens[token1]["decimals"])
        transactions = {
            str(transaction["tx_hash"]).lower(): transaction
            for transaction in source_values.get("transactions", [])
            if isinstance(transaction, Mapping)
        }
        events: list[dict[str, Any]] = []
        addresses = {pair_address}
        raw_records: list[ProtocolRawRecord] = []

        def add_event(
            source_event: Mapping[str, Any],
            suffix: str,
            event_type: str,
            token_address: str,
            amount: object,
            *,
            account_from: str | None = None,
            account_to: str | None = None,
            authorized_supply_change: bool = False,
        ) -> None:
            tx_hash = str(source_event["tx_hash"]).lower()
            log_index = int(source_event["log_index"])
            metadata: dict[str, object] = {
                "protocol": self.name,
                "protocol_event": source_event["event_name"],
                "tx_hash": tx_hash,
                "log_index": log_index,
            }
            if authorized_supply_change:
                metadata["authorized_supply_change"] = True
            for field in ("chain_id", "block_number", "block_hash"):
                if source_event.get(field) is not None:
                    metadata[field] = source_event[field]
            if source.get("rpc_mode") is True:
                metadata["state_scope"] = "partial_history"
            events.append(
                {
                    "external_id": f"{tx_hash}:{log_index}:{suffix}",
                    "event_type": event_type,
                    "occurred_at": transactions[tx_hash]["block_timestamp"],
                    "asset_external_id": token_address,
                    "account_from_external_id": account_from,
                    "account_to_external_id": account_to,
                    "amount": amount,
                    "metadata": metadata,
                }
            )

        for source_event in source_values.get("protocol_events", []):
            if not isinstance(source_event, Mapping):
                continue
            event_name = str(source_event["event_name"])
            tx_hash = str(source_event["tx_hash"]).lower()
            log_index = int(source_event["log_index"])
            raw_records.append(
                ProtocolRawRecord(
                    record_type=f"ethereum.uniswap_v2.{event_name.lower()}",
                    external_id=f"{tx_hash}:{log_index}",
                    payload=dict(source_event),
                )
            )
            sender = (
                _address(source_event["sender"]) if source_event.get("sender") is not None else None
            )
            recipient = _address(source_event["to"]) if source_event.get("to") is not None else None
            if sender is not None:
                addresses.add(sender)
            if recipient is not None:
                addresses.add(recipient)

            if event_name == "Mint":
                add_event(
                    source_event,
                    "mint:token0",
                    "MINT",
                    token0,
                    _amount(source_event["amount0"], decimals0),
                    account_to=pair_address,
                    authorized_supply_change=True,
                )
                add_event(
                    source_event,
                    "mint:token1",
                    "MINT",
                    token1,
                    _amount(source_event["amount1"], decimals1),
                    account_to=pair_address,
                    authorized_supply_change=True,
                )
            elif event_name == "Burn":
                add_event(
                    source_event,
                    "burn:token0",
                    "BURN",
                    token0,
                    _amount(source_event["amount0"], decimals0),
                    account_from=pair_address,
                    authorized_supply_change=True,
                )
                add_event(
                    source_event,
                    "burn:token1",
                    "BURN",
                    token1,
                    _amount(source_event["amount1"], decimals1),
                    account_from=pair_address,
                    authorized_supply_change=True,
                )
            elif event_name == "Swap":
                swap_fields = (
                    ("amount0_in", "in:token0", "DEPOSIT", token0, decimals0, sender, pair_address),
                    ("amount1_in", "in:token1", "DEPOSIT", token1, decimals1, sender, pair_address),
                    (
                        "amount0_out",
                        "out:token0",
                        "WITHDRAWAL",
                        token0,
                        decimals0,
                        pair_address,
                        recipient,
                    ),
                    (
                        "amount1_out",
                        "out:token1",
                        "WITHDRAWAL",
                        token1,
                        decimals1,
                        pair_address,
                        recipient,
                    ),
                )
                for (
                    field,
                    suffix,
                    event_type,
                    token,
                    decimals,
                    account_from,
                    account_to,
                ) in swap_fields:
                    amount = Decimal(str(source_event.get(field, 0)))
                    if amount <= 0:
                        continue
                    add_event(
                        source_event,
                        suffix,
                        event_type,
                        token,
                        _amount(amount, decimals),
                        account_from=account_from,
                        account_to=account_to,
                        authorized_supply_change=True,
                    )
            elif event_name == "Sync":
                add_event(
                    source_event,
                    "sync:token0",
                    "ADJUSTMENT",
                    token0,
                    "0",
                    account_to=pair_address,
                )
                add_event(
                    source_event,
                    "sync:token1",
                    "ADJUSTMENT",
                    token1,
                    "0",
                    account_to=pair_address,
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
    ) -> tuple[InvariantOutcome, ...]:
        source_values = _rpc_source(source)
        pair = source_values["pair"]
        pair_address = _address(pair["address"])
        token0 = _address(pair["token0"])
        token1 = _address(pair["token1"])
        tokens = {
            _address(token["address"]): token
            for token in source_values.get("tokens", [])
            if isinstance(token, Mapping)
        }
        decimals0 = int(tokens[token0]["decimals"])
        decimals1 = int(tokens[token1]["decimals"])
        protocol_events = [
            event
            for event in source_values.get("protocol_events", [])
            if isinstance(event, Mapping)
        ]
        sync_events = [event for event in protocol_events if event.get("event_name") == "Sync"]

        reserve_affected: list[dict[str, object]] = []
        if source.get("rpc_mode") is True:
            tracked: tuple[Decimal, Decimal] | None = None
            for source_event in protocol_events:
                name = source_event.get("event_name")
                if name == "Sync":
                    reported = (
                        Decimal(str(source_event["reserve0"])),
                        Decimal(str(source_event["reserve1"])),
                    )
                    if tracked is not None and tracked != reported:
                        reserve_affected.append(
                            {
                                "external_id": (
                                    f"{source_event['tx_hash']}:{source_event['log_index']}"
                                ),
                                "pair_address": pair_address,
                                "expected_reserve0": str(tracked[0]),
                                "actual_reserve0": str(reported[0]),
                                "expected_reserve1": str(tracked[1]),
                                "actual_reserve1": str(reported[1]),
                            }
                        )
                    tracked = reported
                elif tracked is not None and name in {"Mint", "Burn", "Swap"}:
                    delta0 = Decimal(str(source_event.get("amount0", 0)))
                    delta1 = Decimal(str(source_event.get("amount1", 0)))
                    if name == "Burn":
                        delta0, delta1 = -delta0, -delta1
                    elif name == "Swap":
                        delta0 = Decimal(str(source_event.get("amount0_in", 0))) - Decimal(
                            str(source_event.get("amount0_out", 0))
                        )
                        delta1 = Decimal(str(source_event.get("amount1_in", 0))) - Decimal(
                            str(source_event.get("amount1_out", 0))
                        )
                    tracked = tracked[0] + delta0, tracked[1] + delta1
        elif sync_events:
            latest = sync_events[-1]
            balances = reconstruct_balances(events)
            expected0 = Decimal(_amount(latest["reserve0"], decimals0))
            expected1 = Decimal(_amount(latest["reserve1"], decimals1))
            actual0 = balances.get((pair_address, token0), Decimal(0))
            actual1 = balances.get((pair_address, token1), Decimal(0))
            if (actual0, actual1) != (expected0, expected1):
                reserve_affected.append(
                    {
                        "external_id": f"{latest['tx_hash']}:{latest['log_index']}",
                        "pair_address": pair_address,
                        "expected_reserve0": str(expected0),
                        "actual_reserve0": str(actual0),
                        "expected_reserve1": str(expected1),
                        "actual_reserve1": str(actual1),
                    }
                )

        liquidity_affected: list[dict[str, object]] = []
        previous_sync: tuple[Decimal, Decimal] | None = None
        pending_swap: Mapping[str, Any] | None = None
        for source_event in protocol_events:
            if source_event.get("event_name") == "Swap":
                pending_swap = source_event
            elif source_event.get("event_name") == "Sync":
                current = (
                    Decimal(_amount(source_event["reserve0"], decimals0)),
                    Decimal(_amount(source_event["reserve1"], decimals1)),
                )
                if pending_swap is not None and previous_sync is not None:
                    before_product = previous_sync[0] * previous_sync[1]
                    after_product = current[0] * current[1]
                    if after_product < before_product:
                        liquidity_affected.append(
                            {
                                "external_id": (
                                    f"{pending_swap['tx_hash']}:{pending_swap['log_index']}"
                                ),
                                "pair_address": pair_address,
                                "product_before": str(before_product),
                                "product_after": str(after_product),
                            }
                        )
                    pending_swap = None
                previous_sync = current

        return (
            InvariantOutcome(
                name="reserve_consistency",
                severity="critical",
                description="Uniswap pair reserves match the latest Sync event.",
                affected_records=tuple(reserve_affected),
                protocol_name=self.name,
            ),
            InvariantOutcome(
                name="liquidity_conservation",
                severity="high",
                description="Uniswap swap reserve product does not decrease.",
                affected_records=tuple(liquidity_affected),
                protocol_name=self.name,
            ),
        )
