from datetime import UTC, datetime
from decimal import Decimal

from sentinel.models import EventType
from sentinel.security import (
    AuthorityRegistry,
    CanonicalEvent,
    EvaluationScope,
    InvariantContext,
    canonical_event_order,
    reconstruct_balances,
    run_invariants,
)


def _context(
    *,
    scope: EvaluationScope = EvaluationScope.FULL_STATE,
    authorized: frozenset[str] = frozenset(),
) -> InvariantContext:
    return InvariantContext(
        source_system="bank",
        chain_id=None,
        block_range=None,
        evaluation_scope=scope,
        system_authorized_event_ids=authorized,
    )


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
            metadata={},
            checker_authorized=True,
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
    outcomes = {
        outcome.name: outcome
        for outcome in run_invariants(events, [], _context(authorized=frozenset({"opening"})))
    }

    assert balances == {("A", "USD"): Decimal("900"), ("B", "USD"): Decimal("100")}
    assert outcomes["balance_conservation"].passed
    assert outcomes["no_negative_balances"].passed
    assert outcomes["event_completeness"].passed


def test_payload_cannot_authorize_supply_change_or_claim_full_state() -> None:
    event = CanonicalEvent(
        external_id="forged-mint",
        event_type=EventType.MINT,
        occurred_at=datetime(2026, 1, 1, tzinfo=UTC),
        asset_external_id="USD",
        account_from_external_id="attacker",
        account_to_external_id="attacker",
        amount=Decimal("100"),
        metadata={
            "authorized_supply_change": True,
            "state_scope": "full",
        },
    )

    outcomes = {
        outcome.name: outcome
        for outcome in run_invariants(
            (event,),
            [],
            _context(scope=EvaluationScope.PARTIAL_HISTORY),
        )
    }

    assert outcomes["balance_conservation"].failed
    assert outcomes["no_negative_balances"].insufficient_evidence
    assert outcomes["balance_snapshot_match"].insufficient_evidence


def test_checker_authority_registry_allows_configured_minter() -> None:
    mint = CanonicalEvent(
        external_id="authorized-mint",
        event_type=EventType.MINT,
        occurred_at=datetime(2026, 1, 1, tzinfo=UTC),
        asset_external_id="0xToken",
        account_from_external_id="0xMinter",
        account_to_external_id="0xAlice",
        amount=Decimal("50"),
        metadata={},
        chain_id=1,
        block_number=10,
        transaction_index=0,
        log_index=2,
    )
    context = InvariantContext(
        source_system="ethereum",
        chain_id=1,
        block_range=(10, 10),
        known_authorities=AuthorityRegistry(
            {
                ("ethereum", 1, "0xtoken", "MINT"): frozenset({"0xminter"}),
            }
        ),
    )

    outcomes = {outcome.name: outcome for outcome in run_invariants((mint,), [], context)}

    assert outcomes["balance_conservation"].passed


def test_ethereum_order_uses_block_transaction_and_log_coordinates() -> None:
    later_chain_event = CanonicalEvent(
        external_id="hash-that-sorts-first",
        event_type=EventType.TRANSFER,
        occurred_at=datetime(2020, 1, 1, tzinfo=UTC),
        asset_external_id="TOKEN",
        account_from_external_id="A",
        account_to_external_id="B",
        amount=Decimal("1"),
        metadata={},
        chain_id=1,
        block_number=10,
        transaction_index=4,
        log_index=1,
    )
    earlier_chain_event = CanonicalEvent(
        external_id="hash-that-sorts-last",
        event_type=EventType.TRANSFER,
        occurred_at=datetime(2030, 1, 1, tzinfo=UTC),
        asset_external_id="TOKEN",
        account_from_external_id="B",
        account_to_external_id="A",
        amount=Decimal("1"),
        metadata={},
        chain_id=1,
        block_number=10,
        transaction_index=3,
        log_index=9,
    )

    ordered = sorted((later_chain_event, earlier_chain_event), key=canonical_event_order)

    assert [event.external_id for event in ordered] == [
        "hash-that-sorts-last",
        "hash-that-sorts-first",
    ]


def test_partial_history_is_reported_as_insufficient_evidence() -> None:
    transfer = CanonicalEvent(
        external_id="partial-transfer",
        event_type=EventType.TRANSFER,
        occurred_at=datetime(2026, 1, 1, tzinfo=UTC),
        asset_external_id="USD",
        account_from_external_id="A",
        account_to_external_id="B",
        amount=Decimal("10"),
        metadata={},
    )

    outcomes = {
        outcome.name: outcome
        for outcome in run_invariants(
            (transfer,),
            [],
            _context(scope=EvaluationScope.PARTIAL_HISTORY),
        )
    }

    assert outcomes["balance_conservation"].passed
    assert outcomes["no_negative_balances"].insufficient_evidence
    assert outcomes["balance_snapshot_match"].insufficient_evidence
