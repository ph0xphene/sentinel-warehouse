from collections.abc import Mapping
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from sentinel.models import EventType
from sentinel.security import CanonicalEvent


def _decimal(value: object) -> Decimal | None:
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def build_candidate_events(
    fixture: Mapping[str, Any],
    *,
    has_previous_checkpoint: bool,
) -> tuple[CanonicalEvent, ...]:
    candidates: list[CanonicalEvent] = []
    transaction_times = [
        str(record["occurred_at"])
        for record in fixture.get("transactions", [])
        if isinstance(record, dict) and record.get("occurred_at")
    ]
    explicit_times = [
        str(record["occurred_at"])
        for record in fixture.get("events", [])
        if isinstance(record, dict) and record.get("occurred_at")
    ]
    known_times = [
        datetime.fromisoformat(value.replace("Z", "+00:00"))
        for value in transaction_times + explicit_times
    ]

    if not has_previous_checkpoint:
        for balance in fixture.get("balances", []):
            if not isinstance(balance, dict):
                continue
            as_of = datetime.fromisoformat(str(balance["as_of"]).replace("Z", "+00:00"))
            occurred_at = min(known_times) - timedelta(seconds=1) if known_times else as_of
            candidates.append(
                CanonicalEvent(
                    external_id=f"OPENING:{balance.get('external_id')}",
                    event_type=EventType.CREATE,
                    occurred_at=occurred_at,
                    asset_external_id=(
                        str(balance["asset_external_id"])
                        if balance.get("asset_external_id") is not None
                        else None
                    ),
                    account_from_external_id=None,
                    account_to_external_id=(
                        str(balance["account_external_id"])
                        if balance.get("account_external_id") is not None
                        else None
                    ),
                    amount=_decimal(balance.get("opening_amount")),
                    metadata={
                        "authorized_supply_change": True,
                        "generated_from": "opening_balance",
                    },
                )
            )

    for transaction in fixture.get("transactions", []):
        if not isinstance(transaction, dict):
            continue
        occurred_at = datetime.fromisoformat(str(transaction["occurred_at"]).replace("Z", "+00:00"))
        candidates.append(
            CanonicalEvent(
                external_id=str(transaction.get("external_id")),
                event_type=EventType.TRANSFER,
                occurred_at=occurred_at,
                asset_external_id=(
                    str(transaction["asset_external_id"])
                    if transaction.get("asset_external_id") is not None
                    else None
                ),
                account_from_external_id=(
                    str(transaction["from_account_external_id"])
                    if transaction.get("from_account_external_id") is not None
                    else None
                ),
                account_to_external_id=(
                    str(transaction["to_account_external_id"])
                    if transaction.get("to_account_external_id") is not None
                    else None
                ),
                amount=_decimal(transaction.get("amount")),
                metadata={
                    "description": transaction.get("description"),
                    "generated_from": "transaction",
                },
            )
        )

    for event in fixture.get("events", []):
        if not isinstance(event, dict):
            continue
        occurred_at = datetime.fromisoformat(str(event["occurred_at"]).replace("Z", "+00:00"))
        event_metadata = event.get("metadata")
        candidates.append(
            CanonicalEvent(
                external_id=str(event.get("external_id")),
                event_type=str(event.get("event_type")),
                occurred_at=occurred_at,
                asset_external_id=(
                    str(event["asset_external_id"])
                    if event.get("asset_external_id") is not None
                    else None
                ),
                account_from_external_id=(
                    str(event["account_from_external_id"])
                    if event.get("account_from_external_id") is not None
                    else None
                ),
                account_to_external_id=(
                    str(event["account_to_external_id"])
                    if event.get("account_to_external_id") is not None
                    else None
                ),
                amount=_decimal(event.get("amount")),
                metadata=event_metadata if isinstance(event_metadata, dict) else {},
            )
        )

    return tuple(candidates)
