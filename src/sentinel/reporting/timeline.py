from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from sentinel.ingestion.events import build_candidate_events
from sentinel.security import CanonicalEvent, canonical_event_order


@dataclass(frozen=True)
class TimelineItem:
    external_id: str
    event_type: str
    occurred_at: datetime
    block_number: int | None
    transaction_index: int | None
    log_index: int | None
    account_from: str | None
    account_to: str | None
    asset: str | None
    amount: Decimal | None
    description: str | None
    transaction_hash: str | None

    @property
    def coordinate(self) -> str:
        if self.block_number is not None:
            return (
                f"Block {self.block_number} · transaction "
                f"{self.transaction_index if self.transaction_index is not None else '?'} · "
                f"log {self.log_index if self.log_index is not None else '?'}"
            )
        return self.occurred_at.isoformat()


def _description(
    event: CanonicalEvent,
    flow_descriptions: Mapping[str, str],
) -> str | None:
    flow_description = flow_descriptions.get(event.external_id)
    if flow_description:
        return flow_description
    for field in ("description", "reason"):
        value = event.metadata.get(field)
        if value is not None:
            return str(value)
    return None


def _transaction_hash(event: CanonicalEvent) -> str | None:
    value = event.metadata.get("tx_hash")
    if value is not None:
        return str(value)
    if event.external_id.startswith("0x") and ":" in event.external_id:
        return event.external_id.split(":", 1)[0]
    return None


def build_timeline(
    events: Sequence[CanonicalEvent],
    *,
    flow_descriptions: Mapping[str, str] | None = None,
) -> tuple[TimelineItem, ...]:
    """Create a deterministic presentation timeline from canonical event candidates."""
    descriptions = flow_descriptions or {}
    explicit_events = (
        event for event in events if event.metadata.get("generated_from") != "opening_balance"
    )
    return tuple(
        TimelineItem(
            external_id=event.external_id,
            event_type=str(event.event_type),
            occurred_at=event.occurred_at,
            block_number=event.block_number,
            transaction_index=event.transaction_index,
            log_index=event.log_index,
            account_from=event.account_from_external_id,
            account_to=event.account_to_external_id,
            asset=event.asset_external_id,
            amount=event.amount,
            description=_description(event, descriptions),
            transaction_hash=_transaction_hash(event),
        )
        for event in sorted(explicit_events, key=canonical_event_order)
    )


def timeline_from_fixture(
    fixture: Mapping[str, Any],
    *,
    flow_descriptions: Mapping[str, str] | None = None,
) -> tuple[TimelineItem, ...]:
    events = build_candidate_events(fixture, has_previous_checkpoint=False)
    return build_timeline(events, flow_descriptions=flow_descriptions)
