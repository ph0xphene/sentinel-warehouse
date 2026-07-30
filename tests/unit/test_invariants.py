from datetime import UTC, datetime
from decimal import Decimal

from sentinel.models import EventType
from sentinel.security import CanonicalEvent, reconstruct_balances, run_invariants


def test_transfer_reconstructs_balances_and_conserves_asset() -> None:
    occurred_at = datetime(2026, 1, 1, tzinfo=UTC)
    events = (
        CanonicalEvent(
            external_id="opening",
            event_type=EventType.CREATE,
            occurred_at=occurred_at,
            asset_external_id="USD",
            account_from_external_id=None,
            account_to_external_id="A",
            amount=Decimal("1000"),
            metadata={"authorized_supply_change": True},
        ),
        CanonicalEvent(
            external_id="transfer",
            event_type=EventType.TRANSFER,
            occurred_at=occurred_at,
            asset_external_id="USD",
            account_from_external_id="A",
            account_to_external_id="B",
            amount=Decimal("100"),
            metadata={},
        ),
    )

    balances = reconstruct_balances(events)
    outcomes = {outcome.name: outcome for outcome in run_invariants(events, [])}

    assert balances == {("A", "USD"): Decimal("900"), ("B", "USD"): Decimal("100")}
    assert outcomes["balance_conservation"].passed
    assert outcomes["no_negative_balances"].passed
    assert outcomes["event_completeness"].passed
