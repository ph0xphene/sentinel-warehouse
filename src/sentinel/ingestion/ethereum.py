import json
import uuid
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from sentinel.database import create_database_engine
from sentinel.ingestion.fixture import IngestionSummary, ingest_fixture_payload
from sentinel.models import (
    Account,
    Asset,
    RawEthereumTransaction,
    RawEthereumTransfer,
    RawFinancialRecord,
)
from sentinel.protocols import ProtocolNormalization, detect_protocol
from sentinel.quality import QualityConfig
from sentinel.security import EvaluationScope, InvariantContext

SOURCE_SYSTEM = "ethereum"


def _timestamp(value: object) -> datetime:
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def _address(value: object) -> str:
    return str(value).lower()


def _normalized_amount(value: object, decimals: int) -> str:
    amount = Decimal(str(value)) / (Decimal(10) ** decimals)
    return format(amount, "f")


def _load_source(path: Path) -> tuple[dict[str, Any], bytes]:
    content = path.read_bytes()
    source = json.loads(content)
    if not isinstance(source, dict):
        raise ValueError("Ethereum fixture root must be a JSON object")
    return source, content


def _existing_entities(engine: Engine, source_name: str) -> tuple[set[str], set[str]]:
    with Session(engine) as session:
        accounts = set(
            session.scalars(select(Account.external_id).where(Account.source_name == source_name))
        )
        assets = set(
            session.scalars(select(Asset.external_id).where(Asset.source_name == source_name))
        )
    return accounts, assets


def _normalize_source(
    source: dict[str, Any],
    protocol: ProtocolNormalization,
    engine: Engine,
) -> dict[str, Any]:
    source_name = str(source.get("source_name", SOURCE_SYSTEM))
    existing_accounts, existing_assets = _existing_entities(engine, source_name)
    tokens = {
        _address(token["address"]): token
        for token in (*source.get("tokens", []), *protocol.asset_definitions)
        if isinstance(token, dict)
    }
    address_values = set(protocol.account_addresses)
    for collection in ("opening_balances", "balances"):
        for balance in source.get(collection, []):
            if isinstance(balance, dict):
                address_values.add(_address(balance["address"]))

    accounts = [
        {
            "external_id": address,
            "name": f"Ethereum {address[:10]}",
            "account_type": "ethereum_address",
        }
        for address in sorted(address_values - existing_accounts)
    ]
    assets = [
        {
            "external_id": address,
            "symbol": str(token["symbol"]),
            "name": str(token["name"]),
            "asset_type": "erc20",
            "decimals": int(token["decimals"]),
        }
        for address, token in sorted(tokens.items())
        if address not in existing_assets
    ]
    opening_balances = {
        (_address(balance["address"]), _address(balance["token_address"])): balance["amount"]
        for balance in source.get("opening_balances", [])
        if isinstance(balance, dict)
    }
    checkpoint = str(source["checkpoint"])
    as_of = source["checkpoint_timestamp"]
    balances = []
    for balance in source.get("balances", []):
        if not isinstance(balance, dict):
            continue
        address = _address(balance["address"])
        token_address = _address(balance["token_address"])
        decimals = int(tokens[token_address]["decimals"])
        balances.append(
            {
                "external_id": f"{checkpoint}:{address}:{token_address}",
                "account_external_id": address,
                "asset_external_id": token_address,
                "opening_amount": _normalized_amount(
                    opening_balances.get((address, token_address), 0),
                    decimals,
                ),
                "amount": _normalized_amount(balance["amount"], decimals),
                "as_of": as_of,
            }
        )

    normalized: dict[str, Any] = {
        "source_name": source_name,
        "checkpoint": checkpoint,
        "accounts": accounts,
        "assets": assets,
        "transactions": [],
        "events": list(protocol.events),
        "balances": balances,
    }
    if source.get("previous_checkpoint") is not None:
        normalized["previous_checkpoint"] = str(source["previous_checkpoint"])
    for field in (
        "checkpoint_chain_id",
        "checkpoint_source_identity",
        "checkpoint_block_number",
        "checkpoint_block_hash",
    ):
        if source.get(field) is not None:
            normalized[field] = source[field]
    return normalized


def _raw_stager(source: dict[str, Any], protocol: ProtocolNormalization):
    def stage(session: Session, batch_id: uuid.UUID) -> None:
        session.add_all(
            RawEthereumTransaction(
                batch_id=batch_id,
                tx_hash=str(transaction["tx_hash"]).lower(),
                chain_id=transaction.get("chain_id", source.get("chain_id")),
                block_number=int(transaction["block_number"]),
                transaction_index=(
                    int(transaction["transaction_index"])
                    if transaction.get("transaction_index") is not None
                    else None
                ),
                block_hash=(
                    str(transaction["block_hash"]).lower()
                    if transaction.get("block_hash") is not None
                    else None
                ),
                block_timestamp=_timestamp(transaction["block_timestamp"]),
                from_address=_address(transaction["from_address"]),
                to_address=(
                    _address(transaction["to_address"])
                    if transaction.get("to_address") is not None
                    else None
                ),
                success=bool(transaction["success"]),
                canonical=True,
                transaction_metadata=(
                    transaction["metadata"] if isinstance(transaction.get("metadata"), dict) else {}
                ),
            )
            for transaction in source.get("transactions", [])
            if isinstance(transaction, dict)
        )
        session.add_all(
            RawEthereumTransfer(
                batch_id=batch_id,
                tx_hash=str(transfer["tx_hash"]).lower(),
                log_index=int(transfer["log_index"]),
                chain_id=transfer.get("chain_id", source.get("chain_id")),
                block_number=transfer.get("block_number"),
                transaction_index=(
                    int(transfer["transaction_index"])
                    if transfer.get("transaction_index") is not None
                    else None
                ),
                block_hash=(
                    str(transfer["block_hash"]).lower()
                    if transfer.get("block_hash") is not None
                    else None
                ),
                token_address=_address(transfer["token_address"]),
                from_address=_address(transfer["from_address"]),
                to_address=_address(transfer["to_address"]),
                amount=Decimal(str(transfer["amount"])),
                canonical=True,
            )
            for transfer in protocol.transfers
            if isinstance(transfer, dict)
        )
        session.add_all(
            RawFinancialRecord(
                record_id=uuid.uuid4(),
                batch_id=batch_id,
                source_name=str(source.get("source_name", SOURCE_SYSTEM)),
                record_type=record.record_type,
                external_id=record.external_id,
                payload=record.payload,
                chain_id=record.payload.get("chain_id", source.get("chain_id")),
                block_number=record.payload.get("block_number"),
                block_hash=(
                    str(record.payload["block_hash"]).lower()
                    if record.payload.get("block_hash") is not None
                    else None
                ),
                canonical=True,
            )
            for record in protocol.raw_records
        )

    return stage


def ingest_ethereum_fixture(
    path: Path,
    engine: Engine | None = None,
    *,
    quality_config: QualityConfig | None = None,
) -> IngestionSummary:
    """Detect a protocol plugin and ingest its deterministic Ethereum fixture."""
    engine = engine or create_database_engine()
    source, content = _load_source(path)
    plugin = detect_protocol(source)
    protocol = plugin.normalize(source)
    normalized = _normalize_source(source, protocol, engine)
    return ingest_fixture_payload(
        normalized,
        content,
        engine,
        quality_config=quality_config,
        raw_stager=_raw_stager(source, protocol),
        stage_financial_records=False,
        protocol_plugin=plugin,
        protocol_source=source,
        invariant_context=InvariantContext(
            source_system=str(normalized["source_name"]),
            chain_id=(int(source["chain_id"]) if source.get("chain_id") is not None else None),
            block_range=None,
            evaluation_scope=(
                EvaluationScope.PARTIAL_HISTORY
                if plugin.name == "uniswap_v2"
                else EvaluationScope.FULL_STATE
            ),
        ),
    )
