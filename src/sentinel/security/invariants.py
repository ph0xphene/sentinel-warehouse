import enum
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from sentinel.models import EventType


class EvaluationScope(enum.StrEnum):
    FULL_STATE = "FULL_STATE"
    PARTIAL_HISTORY = "PARTIAL_HISTORY"


class InvariantExecutionResult(enum.StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


AuthorityKey = tuple[str, int | None, str, str]


@dataclass(frozen=True)
class AuthorityRegistry:
    """Checker-owned allowlist for supply-changing event authorities."""

    authorities: Mapping[AuthorityKey, frozenset[str]] = field(default_factory=dict)

    def is_authorized(
        self,
        *,
        source_system: str,
        chain_id: int | None,
        event_type: str,
        asset_external_id: str,
        authority_external_id: str | None,
    ) -> bool:
        if authority_external_id is None:
            return False
        key = (
            source_system,
            chain_id,
            asset_external_id.lower(),
            event_type.upper(),
        )
        allowed = self.authorities.get(key, frozenset())
        return authority_external_id.lower() in {value.lower() for value in allowed}


@dataclass(frozen=True)
class InvariantContext:
    """Trusted checker context supplied separately from untrusted financial events."""

    source_system: str
    chain_id: int | None
    block_range: tuple[int, int] | None
    known_authorities: AuthorityRegistry = field(default_factory=AuthorityRegistry)
    evaluation_scope: EvaluationScope = EvaluationScope.FULL_STATE
    system_authorized_event_ids: frozenset[str] = frozenset()


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
    chain_id: int | None = None
    block_number: int | None = None
    block_hash: str | None = None
    transaction_index: int | None = None
    log_index: int | None = None
    checker_authorized: bool = False


@dataclass(frozen=True)
class InvariantOutcome:
    name: str
    severity: str
    description: str
    affected_records: tuple[dict[str, object], ...]
    protocol_name: str | None = None
    result: InvariantExecutionResult | None = None

    @property
    def execution_result(self) -> str:
        if self.result is not None:
            return self.result.value
        return (
            InvariantExecutionResult.FAILED.value
            if self.affected_records
            else InvariantExecutionResult.PASSED.value
        )

    @property
    def passed(self) -> bool:
        return self.execution_result == InvariantExecutionResult.PASSED

    @property
    def failed(self) -> bool:
        return self.execution_result == InvariantExecutionResult.FAILED

    @property
    def insufficient_evidence(self) -> bool:
        return self.execution_result == InvariantExecutionResult.INSUFFICIENT_EVIDENCE


def canonical_event_order(event: CanonicalEvent) -> tuple[object, ...]:
    """Use chain-native coordinates for Ethereum and time only for non-chain sources."""
    if event.block_number is not None:
        return (
            1,
            event.block_number,
            event.transaction_index if event.transaction_index is not None else -1,
            event.log_index if event.log_index is not None else -1,
        )
    return (0, event.occurred_at, event.external_id)


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
    for event in sorted(events, key=canonical_event_order):
        _apply_event(balances, event)
    return dict(balances)


def _supply_delta(event: CanonicalEvent) -> Decimal | None:
    if event.amount is None or event.asset_external_id is None:
        return None
    delta = Decimal(0)
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
        delta -= event.amount
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
        delta += event.amount
    return delta


def _balance_conservation(
    events: tuple[CanonicalEvent, ...],
    context: InvariantContext,
) -> InvariantOutcome:
    affected: list[dict[str, object]] = []
    insufficient: list[dict[str, object]] = []
    authority_event_types = {
        EventType.CREATE,
        EventType.MINT,
        EventType.BURN,
        EventType.INTEREST,
    }
    boundary_event_types = {
        EventType.DEPOSIT,
        EventType.WITHDRAWAL,
        EventType.FEE,
        EventType.ADJUSTMENT,
    }
    external_id_counts = Counter(event.external_id for event in events)

    for event in events:
        delta = _supply_delta(event)
        if delta is None:
            if event.event_type == EventType.TRANSFER:
                affected.append(
                    {
                        "external_id": event.external_id,
                        "reason": "transfer supply delta cannot be calculated",
                    }
                )
            continue
        if delta == 0:
            continue
        if event.event_type == EventType.TRANSFER:
            affected.append(
                {
                    "external_id": event.external_id,
                    "reason": "transfer changes tracked supply",
                    "supply_delta": str(delta),
                }
            )
            continue

        system_authorized = (
            event.external_id in context.system_authorized_event_ids
            and external_id_counts[event.external_id] == 1
        )
        registry_authorized = (
            event.asset_external_id is not None
            and context.known_authorities.is_authorized(
                source_system=context.source_system,
                chain_id=context.chain_id,
                event_type=event.event_type,
                asset_external_id=event.asset_external_id,
                authority_external_id=event.account_from_external_id,
            )
        )
        if event.event_type in authority_event_types:
            if not system_authorized and not registry_authorized:
                affected.append(
                    {
                        "external_id": event.external_id,
                        "reason": "supply change has no checker-authorized authority",
                        "supply_delta": str(delta),
                    }
                )
            continue

        if (
            event.event_type in boundary_event_types
            and context.evaluation_scope is EvaluationScope.PARTIAL_HISTORY
        ):
            insufficient.append(
                {
                    "external_id": event.external_id,
                    "reason": "tracked boundary lacks complete counterparty state",
                    "supply_delta": str(delta),
                }
            )
            continue

        affected.append(
            {
                "external_id": event.external_id,
                "reason": "unauthorized supply change",
                "supply_delta": str(delta),
            }
        )

    result = None
    records: tuple[dict[str, object], ...] = tuple(affected)
    if not affected and insufficient:
        result = InvariantExecutionResult.INSUFFICIENT_EVIDENCE
        records = tuple(insufficient)
    return InvariantOutcome(
        name="balance_conservation",
        severity="critical",
        description="Transfers conserve value and supply changes require checker authorization.",
        affected_records=records,
        result=result,
    )


def _no_negative_balances(
    events: tuple[CanonicalEvent, ...],
    context: InvariantContext,
) -> InvariantOutcome:
    if context.evaluation_scope is EvaluationScope.PARTIAL_HISTORY:
        return InvariantOutcome(
            name="no_negative_balances",
            severity="high",
            description="An account balance cannot become negative at any event boundary.",
            affected_records=(
                {
                    "reason": "negative balances require a complete opening state",
                    "evaluation_scope": context.evaluation_scope.value,
                },
            ),
            result=InvariantExecutionResult.INSUFFICIENT_EVIDENCE,
        )

    balances: defaultdict[tuple[str, str], Decimal] = defaultdict(Decimal)
    affected: list[dict[str, object]] = []
    reported: set[tuple[str, str]] = set()
    for event in sorted(events, key=canonical_event_order):
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


def _event_completeness(
    events: tuple[CanonicalEvent, ...],
    context: InvariantContext,
) -> InvariantOutcome:
    affected: list[dict[str, object]] = []
    external_id_counts = Counter(event.external_id for event in events)
    for event in events:
        if event.external_id.startswith("OPENING:") and not (
            event.external_id in context.system_authorized_event_ids
            and external_id_counts[event.external_id] == 1
        ):
            affected.append(
                {
                    "external_id": event.external_id,
                    "missing_or_invalid": ["reserved_external_id"],
                }
            )
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
    context: InvariantContext,
) -> InvariantOutcome:
    balances = tuple(reported_balances)
    if context.evaluation_scope is EvaluationScope.PARTIAL_HISTORY and not balances:
        return InvariantOutcome(
            name="balance_snapshot_match",
            severity="high",
            description="Reported balances match balances reconstructed from canonical events.",
            affected_records=(
                {
                    "reason": "no reported balance snapshot is available for the partial range",
                    "evaluation_scope": context.evaluation_scope.value,
                },
            ),
            result=InvariantExecutionResult.INSUFFICIENT_EVIDENCE,
        )

    reconstructed = reconstruct_balances(events)
    affected: list[dict[str, object]] = []
    for balance in balances:
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
    context: InvariantContext,
) -> tuple[InvariantOutcome, ...]:
    event_sequence = tuple(events)
    return (
        _balance_conservation(event_sequence, context),
        _no_negative_balances(event_sequence, context),
        _event_completeness(event_sequence, context),
        _balance_snapshot_match(event_sequence, reported_balances, context),
    )
