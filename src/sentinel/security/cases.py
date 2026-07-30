import json
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, delete, select
from sqlalchemy.orm import Session

from sentinel.database import create_database_engine, session_scope
from sentinel.ingestion.fixture import IngestionSummary, ingest_fixture_payload
from sentinel.models import (
    AttackCategory,
    AttackFlow,
    AttackPattern,
    AttackSubcategory,
    FinancialEvent,
    Incident,
    IncidentCase,
    IncidentFeature,
)

CASE_NAMESPACE = uuid.UUID("406378aa-2812-4f11-84f7-6c9dcda47b81")
PATTERN_NAMESPACE = uuid.UUID("9cc0f87f-1de3-4903-a3a4-43cb0bd90423")
CATEGORY_NAMESPACE = uuid.UUID("950b8a17-f59e-40ed-a846-fe2a031e1816")
SUBCATEGORY_NAMESPACE = uuid.UUID("a03b90b2-279d-43a2-9f64-98e71bf0acf6")
CONFIDENCE_LEVELS = {"low", "medium", "high"}


@dataclass(frozen=True)
class CaseImportSummary:
    case_id: uuid.UUID
    name: str
    attack_pattern: str
    taxonomy: str
    attack_flow_steps: int
    updated: bool


@dataclass(frozen=True)
class CaseReplaySummary:
    case_id: uuid.UUID
    case_name: str
    pipeline: IngestionSummary
    expected_status: str
    actual_status: str
    expected_invariants: frozenset[str]
    actual_invariants: frozenset[str]
    expected_incidents: frozenset[str]
    actual_incidents: frozenset[str]
    incident_ids: tuple[uuid.UUID, ...]
    matched: bool


def _mapping(value: object, name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be a JSON object")
    return dict(value)


def _required_text(values: Mapping[str, Any], name: str) -> str:
    value = values.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"case.{name} must be a non-empty string")
    return value.strip()


def _case_id(case: Mapping[str, Any]) -> uuid.UUID:
    value = case.get("case_id")
    if value is not None:
        try:
            return uuid.UUID(str(value))
        except ValueError as error:
            raise ValueError("case.case_id must be a UUID") from error
    return uuid.uuid5(CASE_NAMESPACE, _required_text(case, "name"))


def _pattern_id(pattern: Mapping[str, Any]) -> uuid.UUID:
    value = pattern.get("pattern_id")
    if value is not None:
        try:
            return uuid.UUID(str(value))
        except ValueError as error:
            raise ValueError("attack_pattern.pattern_id must be a UUID") from error
    identity = f"{_required_text(pattern, 'category')}:{_required_text(pattern, 'name')}"
    return uuid.uuid5(PATTERN_NAMESPACE, identity)


def _taxonomy_id(
    values: Mapping[str, Any],
    field: str,
    namespace: uuid.UUID,
    identity: str,
) -> uuid.UUID:
    value = values.get(field)
    if value is None:
        return uuid.uuid5(namespace, identity)
    try:
        return uuid.UUID(str(value))
    except ValueError as error:
        raise ValueError(f"{field} must be a UUID") from error


def _string_list(values: Mapping[str, Any], field: str, *, allow_empty: bool = False) -> list[str]:
    value = values.get(field)
    if not isinstance(value, list) or not all(
        isinstance(item, str) and bool(item.strip()) for item in value
    ):
        raise ValueError(f"case.{field} must be an array of non-empty strings")
    if not allow_empty and not value:
        raise ValueError(f"case.{field} cannot be empty")
    return [item.strip() for item in value]


def _validate_document(
    document: object,
) -> tuple[
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
    list[dict[str, Any]],
    dict[str, Any],
]:
    root = _mapping(document, "Incident case fixture")
    case = _mapping(root.get("case"), "case")
    for field in (
        "name",
        "protocol",
        "chain",
        "category",
        "severity",
        "confidence_level",
        "description",
    ):
        _required_text(case, field)
    if str(case["confidence_level"]).lower() not in CONFIDENCE_LEVELS:
        raise ValueError("case.confidence_level must be low, medium, or high")
    _string_list(case, "affected_contracts")
    _string_list(case, "attacker_addresses", allow_empty=True)
    _string_list(case, "reference_transactions")
    _string_list(case, "external_references")

    taxonomy = _mapping(root.get("taxonomy"), "taxonomy")
    taxonomy_category = _mapping(taxonomy.get("category"), "taxonomy.category")
    taxonomy_subcategory = _mapping(
        taxonomy.get("subcategory"),
        "taxonomy.subcategory",
    )
    for values in (taxonomy_category, taxonomy_subcategory):
        for field in ("name", "description"):
            _required_text(values, field)

    pattern = _mapping(root.get("attack_pattern"), "attack_pattern")
    for field in ("name", "category", "description"):
        _required_text(pattern, field)

    flow_value = root.get("attack_flow")
    if not isinstance(flow_value, list):
        raise ValueError("attack_flow must be an array")
    flows = [_mapping(item, "attack_flow item") for item in flow_value]
    step_numbers: set[int] = set()
    for flow in flows:
        step = flow.get("step_number")
        if not isinstance(step, int) or isinstance(step, bool) or step <= 0:
            raise ValueError("attack_flow.step_number must be a positive integer")
        if step in step_numbers:
            raise ValueError(f"Duplicate attack flow step {step}")
        step_numbers.add(step)
        _required_text(flow, "action")
        _required_text(flow, "description")
        if flow.get("event_id") is not None:
            try:
                uuid.UUID(str(flow["event_id"]))
            except ValueError as error:
                raise ValueError("attack_flow.event_id must be a UUID or null") from error

    replay = _mapping(root.get("replay"), "replay")
    fixture = _mapping(replay.get("fixture"), "replay.fixture")
    if not isinstance(fixture.get("source_name"), str):
        raise ValueError("replay.fixture.source_name must be a string")
    expected = _mapping(replay.get("expected"), "replay.expected")
    if expected.get("status") not in {"succeeded", "failed"}:
        raise ValueError("replay.expected.status must be succeeded or failed")
    for field in ("invariants", "incidents"):
        values = expected.get(field, [])
        if not isinstance(values, list) or not all(isinstance(item, str) for item in values):
            raise ValueError(f"replay.expected.{field} must be an array of strings")
    return (
        case,
        taxonomy_category,
        taxonomy_subcategory,
        pattern,
        flows,
        replay,
    )


def import_incident_case(
    path: Path,
    engine: Engine | None = None,
) -> CaseImportSummary:
    """Validate and transactionally import one self-contained incident case."""
    engine = engine or create_database_engine()
    document = json.loads(path.read_text())
    (
        case_values,
        category_values,
        subcategory_values,
        pattern_values,
        flow_values,
        replay,
    ) = _validate_document(document)
    case_id = _case_id(case_values)
    pattern_id = _pattern_id(pattern_values)
    category_name = _required_text(category_values, "name")
    category_id = _taxonomy_id(
        category_values,
        "category_id",
        CATEGORY_NAMESPACE,
        category_name,
    )
    subcategory_name = _required_text(subcategory_values, "name")
    subcategory_id = _taxonomy_id(
        subcategory_values,
        "subcategory_id",
        SUBCATEGORY_NAMESPACE,
        f"{category_id}:{subcategory_name}",
    )

    with session_scope(engine) as session:
        category = session.get(AttackCategory, category_id)
        existing_category_name = session.scalar(
            select(AttackCategory).where(AttackCategory.name == category_name)
        )
        if existing_category_name is not None and existing_category_name.category_id != category_id:
            raise ValueError(
                f"Taxonomy category {category_name!r} already belongs to "
                f"{existing_category_name.category_id}"
            )
        if category is None:
            category = AttackCategory(category_id=category_id)
            session.add(category)
        category.name = category_name
        category.description = _required_text(category_values, "description")

        subcategory = session.get(AttackSubcategory, subcategory_id)
        existing_subcategory_name = session.scalar(
            select(AttackSubcategory).where(
                AttackSubcategory.category_id == category_id,
                AttackSubcategory.name == subcategory_name,
            )
        )
        if (
            existing_subcategory_name is not None
            and existing_subcategory_name.subcategory_id != subcategory_id
        ):
            raise ValueError(
                f"Taxonomy subcategory {subcategory_name!r} already belongs to "
                f"{existing_subcategory_name.subcategory_id}"
            )
        if subcategory is None:
            subcategory = AttackSubcategory(subcategory_id=subcategory_id)
            session.add(subcategory)
        subcategory.category_id = category_id
        subcategory.name = subcategory_name
        subcategory.description = _required_text(subcategory_values, "description")

        pattern = session.get(AttackPattern, pattern_id)
        existing_pattern_name = session.scalar(
            select(AttackPattern).where(AttackPattern.name == pattern_values["name"])
        )
        if existing_pattern_name is not None and existing_pattern_name.pattern_id != pattern_id:
            raise ValueError(
                f"Attack pattern name {pattern_values['name']!r} already belongs to "
                f"{existing_pattern_name.pattern_id}"
            )
        if pattern is None:
            pattern = AttackPattern(pattern_id=pattern_id)
            session.add(pattern)
        pattern.subcategory_id = subcategory_id
        pattern.name = _required_text(pattern_values, "name")
        pattern.category = _required_text(pattern_values, "category")
        pattern.description = _required_text(pattern_values, "description")

        case = session.get(IncidentCase, case_id)
        existing_name = session.scalar(
            select(IncidentCase).where(IncidentCase.name == case_values["name"])
        )
        if existing_name is not None and existing_name.case_id != case_id:
            raise ValueError(
                f"Case name {case_values['name']!r} already belongs to {existing_name.case_id}"
            )
        updated = case is not None
        if case is None:
            case = IncidentCase(case_id=case_id)
            session.add(case)
        case.attack_pattern_id = pattern_id
        case.name = _required_text(case_values, "name")
        case.protocol = _required_text(case_values, "protocol")
        case.chain = _required_text(case_values, "chain")
        case.category = _required_text(case_values, "category")
        case.severity = _required_text(case_values, "severity")
        case.confidence_level = _required_text(case_values, "confidence_level").lower()
        case.affected_contracts = _string_list(case_values, "affected_contracts")
        case.attacker_addresses = _string_list(
            case_values,
            "attacker_addresses",
            allow_empty=True,
        )
        case.reference_transactions = _string_list(case_values, "reference_transactions")
        case.external_references = _string_list(case_values, "external_references")
        case.description = _required_text(case_values, "description")
        case.replay_definition = replay
        session.flush()

        session.execute(delete(IncidentFeature).where(IncidentFeature.case_id == case_id))
        session.execute(delete(AttackFlow).where(AttackFlow.case_id == case_id))
        session.add_all(
            AttackFlow(
                flow_id=uuid.uuid4(),
                case_id=case_id,
                step_number=int(flow["step_number"]),
                event_id=(
                    uuid.UUID(str(flow["event_id"])) if flow.get("event_id") is not None else None
                ),
                action=_required_text(flow, "action"),
                description=_required_text(flow, "description"),
            )
            for flow in flow_values
        )

    return CaseImportSummary(
        case_id=case_id,
        name=_required_text(case_values, "name"),
        attack_pattern=_required_text(pattern_values, "name"),
        taxonomy=f"{category_name} / {subcategory_name}",
        attack_flow_steps=len(flow_values),
        updated=updated,
    )


def _link_attack_flow_events(
    session: Session,
    case: IncidentCase,
    source_name: str,
) -> None:
    event_ids = {
        event.external_id: event.event_id
        for event in session.scalars(
            select(FinancialEvent).where(
                FinancialEvent.source_system == source_name,
                FinancialEvent.canonical.is_(True),
            )
        )
    }
    flow_links = case.replay_definition.get("flow_event_external_ids", {})
    if not isinstance(flow_links, Mapping):
        return
    for flow in session.scalars(select(AttackFlow).where(AttackFlow.case_id == case.case_id)):
        external_id = flow_links.get(str(flow.step_number))
        if external_id is not None:
            flow.event_id = event_ids.get(str(external_id))


def replay_incident_case(
    case_id: uuid.UUID,
    engine: Engine | None = None,
) -> CaseReplaySummary:
    """Replay a stored case through the existing financial ingestion pipeline."""
    engine = engine or create_database_engine()
    with Session(engine) as session:
        case = session.get(IncidentCase, case_id)
        if case is None:
            raise ValueError(f"Incident case {case_id} was not found")
        case_name = case.name
        replay = dict(case.replay_definition)

    fixture = _mapping(replay.get("fixture"), "replay.fixture")
    expected = _mapping(replay.get("expected"), "replay.expected")
    source_content = json.dumps(fixture, sort_keys=True, separators=(",", ":")).encode()
    pipeline = ingest_fixture_payload(fixture, source_content, engine)

    with session_scope(engine) as session:
        case = session.get(IncidentCase, case_id)
        if case is None:
            raise RuntimeError(f"Incident case {case_id} disappeared during replay")
        _link_attack_flow_events(session, case, str(fixture["source_name"]))
        incidents = tuple(
            session.execute(
                select(Incident.incident_id, Incident.incident_type)
                .where(Incident.batch_id == pipeline.batch_id)
                .order_by(Incident.incident_type, Incident.incident_id)
            ).all()
        )

    expected_status = str(expected["status"])
    expected_invariants = frozenset(str(value) for value in expected.get("invariants", []))
    actual_invariants = frozenset(
        outcome.name for outcome in pipeline.invariant_results if not outcome.passed
    )
    expected_incidents = frozenset(str(value) for value in expected.get("incidents", []))
    actual_incidents = frozenset(incident.incident_type for incident in incidents)
    actual_status = pipeline.status.value
    matched = (
        actual_status == expected_status
        and actual_invariants == expected_invariants
        and actual_incidents == expected_incidents
    )
    return CaseReplaySummary(
        case_id=case_id,
        case_name=case_name,
        pipeline=pipeline,
        expected_status=expected_status,
        actual_status=actual_status,
        expected_invariants=expected_invariants,
        actual_invariants=actual_invariants,
        expected_incidents=expected_incidents,
        actual_incidents=actual_incidents,
        incident_ids=tuple(incident.incident_id for incident in incidents),
        matched=matched,
    )
