from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sentinel.models import EventType


@dataclass(frozen=True)
class CanonicalEvent:
    external_id: str
    event_type: str
    occurred_at: datetime
    asset_external_id: str | None
    account_from_external_id: str | None
    account_to_external_id: str | None
    amount: Decimal | None
    metadata: dict[str, object]


@dataclass(frozen=True)
class InvariantOutcome:
    name: str
    severity: str
    description: str
    affected_records: tuple[dict[str, object], ...]
    protocol_name: str | None = None

    @property
    def passed(self) -> bool:
        return not self.affected_records

    @property
    def execution_result(self) -> str:
        return "passed" if self.passed else "failed"


def _apply_event(
    balances: defaultdict[tuple[str, str], Decimal],
    event: CanonicalEvent,
) -> None:
    if event.asset_external_id is None or event.amount is None:
        return
    asset = event.asset_external_id
    if event.event_type == EventType.TRANSFER:
        if event.account_from_external_id is not None:
            balances[(event.account_from_external_id, asset)] -= event.amount
        if event.account_to_external_id is not None:
            balances[(event.account_to_external_id, asset)] += event.amount
        return
    if event.event_type in {
        EventType.CREATE,
        EventType.MINT,
        EventType.DEPOSIT,
        EventType.INTEREST,
    }:
        if event.account_to_external_id is not None:
            balances[(event.account_to_external_id, asset)] += event.amount
        return
    if event.event_type in {
        EventType.BURN,
        EventType.WITHDRAWAL,
        EventType.FEE,
    }:
        if event.account_from_external_id is not None:
            balances[(event.account_from_external_id, asset)] -= event.amount
        return
    if event.event_type == EventType.ADJUSTMENT:
        if event.account_from_external_id is not None:
            balances[(event.account_from_external_id, asset)] -= event.amount
        if event.account_to_external_id is not None:
            balances[(event.account_to_external_id, asset)] += event.amount


def reconstruct_balances(
    events: Iterable[CanonicalEvent],
) -> dict[tuple[str, str], Decimal]:
    balances: defaultdict[tuple[str, str], Decimal] = defaultdict(Decimal)
    for event in sorted(events, key=lambda item: (item.occurred_at, item.external_id)):
        _apply_event(balances, event)
    return dict(balances)


def _balance_conservation(events: tuple[CanonicalEvent, ...]) -> InvariantOutcome:
    affected: list[dict[str, object]] = []
    for event in events:
        if event.amount is None or event.asset_external_id is None:
            if event.event_type == EventType.TRANSFER:
                affected.append(
                    {
                        "external_id": event.external_id,
                        "reason": "transfer supply delta cannot be calculated",
                    }
                )
            continue

        before_after_delta = Decimal(0)
        if (
            event.event_type
            in {
                EventType.TRANSFER,
                EventType.BURN,
                EventType.WITHDRAWAL,
                EventType.FEE,
                EventType.ADJUSTMENT,
            }
            and event.account_from_external_id is not None
        ):
            before_after_delta -= event.amount
        if (
            event.event_type
            in {
                EventType.CREATE,
                EventType.MINT,
                EventType.TRANSFER,
                EventType.DEPOSIT,
                EventType.INTEREST,
                EventType.ADJUSTMENT,
            }
            and event.account_to_external_id is not None
        ):
            before_after_delta += event.amount

        if event.event_type == EventType.TRANSFER and before_after_delta != 0:
            affected.append(
                {
                    "external_id": event.external_id,
                    "reason": "transfer changes tracked supply",
                    "supply_delta": str(before_after_delta),
                }
            )
        elif before_after_delta != 0 and event.metadata.get("authorized_supply_change") is not True:
            affected.append(
                {
                    "external_id": event.external_id,
                    "reason": "unauthorized supply change",
                    "supply_delta": str(before_after_delta),
                }
            )
    return InvariantOutcome(
        name="balance_conservation",
        severity="critical",
        description="Transfers conserve asset totals and supply changes require authorization.",
        affected_records=tuple(affected),
    )


def _no_negative_balances(events: tuple[CanonicalEvent, ...]) -> InvariantOutcome:
    balances: defaultdict[tuple[str, str], Decimal] = defaultdict(Decimal)
    affected: list[dict[str, object]] = []
    reported: set[tuple[str, str]] = set()
    for event in sorted(events, key=lambda item: (item.occurred_at, item.external_id)):
        if event.metadata.get("state_scope") == "partial_history":
            continue
        _apply_event(balances, event)
        for (account, asset), balance in balances.items():
            key = (account, asset)
            if balance < 0 and key not in reported:
                affected.append(
                    {
                        "external_id": event.external_id,
                        "account_external_id": account,
                        "asset_external_id": asset,
                        "balance": str(balance),
                    }
                )
                reported.add(key)
    return InvariantOutcome(
        name="no_negative_balances",
        severity="high",
        description="An account balance cannot become negative at any event boundary.",
        affected_records=tuple(affected),
    )


def _event_completeness(events: tuple[CanonicalEvent, ...]) -> InvariantOutcome:
    affected: list[dict[str, object]] = []
    for event in events:
        if event.event_type != EventType.TRANSFER:
            continue
        missing = []
        if event.account_from_external_id is None:
            missing.append("account_from")
        if event.account_to_external_id is None:
            missing.append("account_to")
        if event.asset_external_id is None:
            missing.append("asset")
        if event.amount is None or event.amount <= 0:
            missing.append("positive_amount")
        if missing:
            affected.append({"external_id": event.external_id, "missing_or_invalid": missing})
    return InvariantOutcome(
        name="event_completeness",
        severity="critical",
        description="Every transfer has both accounts, an asset, and a positive amount.",
        affected_records=tuple(affected),
    )


def _balance_snapshot_match(
    events: tuple[CanonicalEvent, ...],
    reported_balances: Iterable[Mapping[str, object]],
) -> InvariantOutcome:
    reconstructed = reconstruct_balances(events)
    affected: list[dict[str, object]] = []
    for balance in reported_balances:
        account = str(balance.get("account_external_id"))
        asset = str(balance.get("asset_external_id"))
        try:
            reported = Decimal(str(balance.get("amount")))
        except Exception:
            affected.append(
                {
                    "external_id": balance.get("external_id"),
                    "reason": "reported balance is not numeric",
                }
            )
            continue
        expected = reconstructed.get((account, asset), Decimal(0))
        if expected != reported:
            affected.append(
                {
                    "external_id": balance.get("external_id"),
                    "account_external_id": account,
                    "asset_external_id": asset,
                    "expected": str(expected),
                    "reported": str(reported),
                    "difference": str(reported - expected),
                }
            )
    return InvariantOutcome(
        name="balance_snapshot_match",
        severity="high",
        description="Reported balances match balances reconstructed from canonical events.",
        affected_records=tuple(affected),
    )


def run_invariants(
    events: Iterable[CanonicalEvent],
    reported_balances: Iterable[Mapping[str, object]],
) -> tuple[InvariantOutcome, ...]:
    event_sequence = tuple(events)
    return (
        _balance_conservation(event_sequence),
        _no_negative_balances(event_sequence),
        _event_completeness(event_sequence),
        _balance_snapshot_match(event_sequence, reported_balances),
    )
