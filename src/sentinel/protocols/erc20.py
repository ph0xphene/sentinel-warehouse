from collections.abc import Mapping, Sequence
from decimal import Decimal
from typing import Any

from sentinel.ethereum.abi import decode_topic_address, decode_words
from sentinel.protocols.base import ProtocolNormalization
from sentinel.security import CanonicalEvent, InvariantContext, InvariantOutcome

TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"


def _address(value: object) -> str:
    return str(value).lower()


def _amount(value: object, decimals: int) -> str:
    return format(Decimal(str(value)) / (Decimal(10) ** decimals), "f")


class ERC20TransferPlugin:
    name = "erc20"

    def detect(self, source: Mapping[str, Any]) -> bool:
        if source.get("protocol") not in (None, "", "erc20"):
            return False
        if source.get("transfers"):
            return True
        return any(
            isinstance(log, Mapping)
            and bool(log.get("topics"))
            and str(log["topics"][0]).lower() == TRANSFER_TOPIC
            for log in source.get("rpc_logs", [])
        )

    def normalize(self, source: Mapping[str, Any]) -> ProtocolNormalization:
        tokens: dict[str, Mapping[str, Any]] = {
            _address(token["address"]): token
            for token in source.get("tokens", [])
            if isinstance(token, Mapping)
        }
        transactions: dict[str, Mapping[str, Any]] = {
            str(transaction["tx_hash"]).lower(): transaction
            for transaction in source.get("transactions", [])
            if isinstance(transaction, Mapping)
        }
        transfers = [
            dict(transfer)
            for transfer in source.get("transfers", [])
            if isinstance(transfer, Mapping)
        ]

        for log in source.get("rpc_logs", []):
            if (
                not isinstance(log, Mapping)
                or not log.get("topics")
                or str(log["topics"][0]).lower() != TRANSFER_TOPIC
            ):
                continue
            topics = log["topics"]
            if not isinstance(topics, Sequence) or isinstance(topics, (str, bytes)):
                raise ValueError("ERC-20 log topics must be an array")
            if len(topics) < 3:
                raise ValueError("ERC-20 Transfer log requires three topics")
            token_address = _address(log["address"])
            tx_hash = str(log["transaction_hash"]).lower()
            transfer = {
                "tx_hash": tx_hash,
                "log_index": int(log["log_index"]),
                "token_address": token_address,
                "from_address": decode_topic_address(topics[1]),
                "to_address": decode_topic_address(topics[2]),
                "amount": decode_words(log.get("data", "0x"), 1)[0],
                "chain_id": int(source["chain_id"]),
                "block_number": int(log["block_number"]),
                "transaction_index": int(log.get("transaction_index", 0)),
                "block_hash": str(log["block_hash"]).lower(),
            }
            transfers.append(transfer)
            transactions.setdefault(
                tx_hash,
                {
                    "tx_hash": tx_hash,
                    "block_number": int(log["block_number"]),
                    "block_hash": str(log["block_hash"]).lower(),
                    "block_timestamp": log["block_timestamp"],
                    "transaction_index": int(log.get("transaction_index", 0)),
                    "success": True,
                },
            )

        events: list[dict[str, Any]] = []
        addresses: set[str] = set()
        for transfer in transfers:
            tx_hash = str(transfer["tx_hash"]).lower()
            from_address = _address(transfer["from_address"])
            to_address = _address(transfer["to_address"])
            token_address = _address(transfer["token_address"])
            log_index = int(transfer["log_index"])
            addresses.update((from_address, to_address))
            transaction = transactions[tx_hash]
            if not bool(transaction["success"]):
                continue
            metadata: dict[str, object] = {
                "protocol": self.name,
                "tx_hash": tx_hash,
                "log_index": log_index,
                "block_number": int(transaction["block_number"]),
                "token_address": token_address,
            }
            if transfer.get("chain_id") is not None:
                metadata["chain_id"] = int(transfer["chain_id"])
            if transfer.get("block_hash") is not None:
                metadata["block_hash"] = str(transfer["block_hash"]).lower()
            token = tokens.get(token_address)
            decimals = int(token["decimals"]) if token is not None else None
            metadata["asset_metadata_status"] = "known" if token is not None else "unknown"
            events.append(
                {
                    "external_id": f"{tx_hash}:{log_index}",
                    "event_type": "TRANSFER",
                    "occurred_at": transaction["block_timestamp"],
                    "asset_external_id": token_address,
                    "account_from_external_id": from_address,
                    "account_to_external_id": to_address,
                    "amount": (
                        _amount(transfer["amount"], decimals)
                        if decimals is not None
                        else format(Decimal(str(transfer["amount"])), "f")
                    ),
                    "chain_id": (
                        int(transfer["chain_id"]) if transfer.get("chain_id") is not None else None
                    ),
                    "block_number": int(transaction["block_number"]),
                    "block_hash": (
                        str(transfer["block_hash"]).lower()
                        if transfer.get("block_hash") is not None
                        else None
                    ),
                    "transaction_index": int(
                        transfer.get(
                            "transaction_index",
                            transaction.get("transaction_index", 0),
                        )
                    ),
                    "log_index": log_index,
                    "metadata": metadata,
                }
            )

        return ProtocolNormalization(
            events=tuple(events),
            account_addresses=frozenset(addresses),
            raw_records=(),
            transfers=tuple(transfers),
            asset_definitions=tuple(dict(token) for token in tokens.values()),
        )

    def invariants(
        self,
        events: tuple[CanonicalEvent, ...],
        source: Mapping[str, Any],
        context: InvariantContext,
    ) -> tuple[InvariantOutcome, ...]:
        return ()
