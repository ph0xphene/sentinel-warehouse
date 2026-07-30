import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from sqlalchemy import Engine, delete, select
from sqlalchemy.orm import Session

from sentinel.database import create_database_engine, session_scope
from sentinel.ingestion.events import build_candidate_events
from sentinel.models import AttackFlow, IncidentCase, IncidentFeature
from sentinel.security.cases import replay_incident_case
from sentinel.security.invariants import CanonicalEvent, canonical_event_order

EXTRACTION_VERSION = "3.0.0"
NUMERIC_FEATURES = (
    "number_of_events",
    "number_of_accounts",
    "transferred_volume",
    "balance_delta",
    "event_sequence_length",
    "unique_contracts_count",
    "time_window_duration",
    "asset_transition_graph_size",
)
CATEGORICAL_FEATURES = (
    "affected_assets",
    "failed_invariants",
    "protocol_type",
    "event_type_sequence",
    "invariant_failure_category",
)
FEATURE_NAMES = (*NUMERIC_FEATURES, *CATEGORICAL_FEATURES)


@dataclass(frozen=True)
class ExtractedFeature:
    name: str
    numeric_value: Decimal | None = None
    categorical_value: str | None = None

    @property
    def value(self) -> Decimal | str:
        if self.numeric_value is not None:
            return self.numeric_value
        if self.categorical_value is not None:
            return self.categorical_value
        raise RuntimeError(f"Feature {self.name} has no value")


@dataclass(frozen=True)
class FeatureExtractionSummary:
    case_id: uuid.UUID
    case_name: str
    batch_id: uuid.UUID
    replay_matched: bool
    extraction_version: str
    features: tuple[ExtractedFeature, ...]


def _event_supply_delta(event: CanonicalEvent) -> Decimal:
    if event.amount is None or event.metadata.get("generated_from") == "opening_balance":
        return Decimal(0)
    delta = Decimal(0)
    if (
        event.event_type in {"TRANSFER", "BURN", "WITHDRAWAL", "FEE", "ADJUSTMENT"}
        and event.account_from_external_id is not None
    ):
        delta -= event.amount
    if (
        event.event_type
        in {
            "CREATE",
            "MINT",
            "TRANSFER",
            "DEPOSIT",
            "INTEREST",
            "ADJUSTMENT",
        }
        and event.account_to_external_id is not None
    ):
        delta += event.amount
    return delta


def _transferred_volume(events: tuple[CanonicalEvent, ...]) -> Decimal:
    return sum(
        (
            abs(event.amount)
            for event in events
            if event.amount is not None
            and event.metadata.get("generated_from") != "opening_balance"
        ),
        start=Decimal(0),
    )


INVARIANT_CATEGORIES = {
    "balance_conservation": "accounting_integrity",
    "balance_snapshot_match": "reconciliation",
    "no_negative_balances": "state_validity",
    "event_completeness": "event_integrity",
    "reserve_consistency": "protocol_state",
    "liquidity_conservation": "protocol_economics",
}


def _invariant_categories(failed_invariants: frozenset[str]) -> str:
    categories = sorted(
        {INVARIANT_CATEGORIES.get(invariant, "other") for invariant in failed_invariants}
    )
    return "|".join(categories) or "none"


def _feature_values(
    case: IncidentCase,
    fixture: dict[str, Any],
    failed_invariants: frozenset[str],
    event_sequence_length: int,
) -> tuple[ExtractedFeature, ...]:
    events = build_candidate_events(fixture, has_previous_checkpoint=False)
    explicit_events = tuple(
        sorted(
            (
                event
                for event in events
                if event.metadata.get("generated_from") != "opening_balance"
            ),
            key=canonical_event_order,
        )
    )
    explicit_event_count = sum(
        len(fixture.get(collection, [])) for collection in ("transactions", "events")
    )
    affected_assets = sorted(
        {
            event.asset_external_id
            for event in events
            if event.asset_external_id is not None
            and event.metadata.get("generated_from") != "opening_balance"
        }
    )
    balance_delta = sum((_event_supply_delta(event) for event in events), start=Decimal(0))
    event_times = [event.occurred_at for event in explicit_events]
    time_window = (
        Decimal(str((max(event_times) - min(event_times)).total_seconds()))
        if event_times
        else Decimal(0)
    )
    transition_edges = {
        (
            event.account_from_external_id or "SOURCE",
            event.account_to_external_id or "SINK",
            event.asset_external_id or "UNKNOWN",
        )
        for event in explicit_events
    }
    contracts = case.affected_contracts or []
    return (
        ExtractedFeature("number_of_events", numeric_value=Decimal(explicit_event_count)),
        ExtractedFeature(
            "number_of_accounts",
            numeric_value=Decimal(len(fixture.get("accounts", []))),
        ),
        ExtractedFeature(
            "affected_assets",
            categorical_value="|".join(affected_assets) or "none",
        ),
        ExtractedFeature(
            "transferred_volume",
            numeric_value=_transferred_volume(events),
        ),
        ExtractedFeature("balance_delta", numeric_value=balance_delta),
        ExtractedFeature(
            "failed_invariants",
            categorical_value="|".join(sorted(failed_invariants)) or "none",
        ),
        ExtractedFeature("protocol_type", categorical_value=case.protocol),
        ExtractedFeature(
            "event_sequence_length",
            numeric_value=Decimal(event_sequence_length),
        ),
        ExtractedFeature(
            "event_type_sequence",
            categorical_value=">".join(event.event_type for event in explicit_events) or "none",
        ),
        ExtractedFeature(
            "unique_contracts_count",
            numeric_value=Decimal(len(set(contracts))),
        ),
        ExtractedFeature("time_window_duration", numeric_value=time_window),
        ExtractedFeature(
            "asset_transition_graph_size",
            numeric_value=Decimal(len(transition_edges)),
        ),
        ExtractedFeature(
            "invariant_failure_category",
            categorical_value=_invariant_categories(failed_invariants),
        ),
    )


def extract_case_features(
    case_id: uuid.UUID,
    engine: Engine | None = None,
) -> FeatureExtractionSummary:
    """Replay a case and atomically replace its deterministic feature set."""
    engine = engine or create_database_engine()
    replay = replay_incident_case(case_id, engine)
    with Session(engine) as session:
        case = session.get(IncidentCase, case_id)
        if case is None:
            raise ValueError(f"Incident case {case_id} was not found")
        if case.attack_pattern_id is None:
            raise ValueError(f"Incident case {case_id} has no attack-pattern label")
        fixture_value = case.replay_definition.get("fixture")
        if not isinstance(fixture_value, dict):
            raise ValueError(f"Incident case {case_id} has no valid replay fixture")
        fixture = dict(fixture_value)
        case_name = case.name
        event_sequence_length = len(
            tuple(session.scalars(select(AttackFlow.flow_id).where(AttackFlow.case_id == case_id)))
        )
        features = _feature_values(
            case,
            fixture,
            replay.actual_invariants,
            event_sequence_length,
        )

    with session_scope(engine) as session:
        session.execute(delete(IncidentFeature).where(IncidentFeature.case_id == case_id))
        session.add_all(
            IncidentFeature(
                case_id=case_id,
                feature_name=feature.name,
                numeric_value=feature.numeric_value,
                categorical_value=feature.categorical_value,
            )
            for feature in features
        )

    return FeatureExtractionSummary(
        case_id=case_id,
        case_name=case_name,
        batch_id=replay.pipeline.batch_id,
        replay_matched=replay.matched,
        extraction_version=EXTRACTION_VERSION,
        features=tuple(sorted(features, key=lambda feature: feature.name)),
    )
