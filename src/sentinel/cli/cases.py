import json
import uuid
from pathlib import Path

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from sentinel.database import create_database_engine
from sentinel.models import (
    AttackCategory,
    AttackFlow,
    AttackPattern,
    AttackSubcategory,
    IncidentCase,
)
from sentinel.security.cases import import_incident_case, replay_incident_case
from sentinel.security.features import extract_case_features


def list_cases(engine: Engine | None = None) -> int:
    engine = engine or create_database_engine()
    with Session(engine) as session:
        cases = tuple(
            session.scalars(select(IncidentCase).order_by(IncidentCase.name, IncidentCase.case_id))
        )
    if not cases:
        print("No incident cases found.")
        return 0
    print(
        "CASE_ID                               SEVERITY  PROTOCOL          CATEGORY          NAME"
    )
    for case in cases:
        print(
            f"{case.case_id}  {case.severity:<8}  {case.protocol:<16}  "
            f"{case.category:<16}  {case.name}"
        )
    return 0


def show_case(case_id: uuid.UUID, engine: Engine | None = None) -> int:
    engine = engine or create_database_engine()
    with Session(engine) as session:
        case = session.get(IncidentCase, case_id)
        if case is None:
            print(f"Incident case {case_id} was not found.")
            return 1
        flows = tuple(
            session.scalars(
                select(AttackFlow)
                .where(AttackFlow.case_id == case_id)
                .order_by(AttackFlow.step_number, AttackFlow.flow_id)
            )
        )
        label = session.execute(
            select(AttackPattern, AttackSubcategory, AttackCategory)
            .join(
                AttackSubcategory,
                AttackPattern.subcategory_id == AttackSubcategory.subcategory_id,
            )
            .join(
                AttackCategory,
                AttackSubcategory.category_id == AttackCategory.category_id,
            )
            .where(AttackPattern.pattern_id == case.attack_pattern_id)
        ).one_or_none()
        replay = case.replay_definition
        fixture = replay.get("fixture", {})
        values = {
            "case_id": str(case.case_id),
            "name": case.name,
            "protocol": case.protocol,
            "chain": case.chain,
            "category": case.category,
            "severity": case.severity,
            "confidence_level": case.confidence_level,
            "affected_contracts": case.affected_contracts,
            "attacker_addresses": case.attacker_addresses,
            "reference_transactions": case.reference_transactions,
            "external_references": case.external_references,
            "description": case.description,
            "created_at": case.created_at.isoformat(),
            "replay": {
                "source_name": (fixture.get("source_name") if isinstance(fixture, dict) else None),
                "expected": replay.get("expected"),
            },
            "attack_flow": [
                {
                    "step_number": flow.step_number,
                    "event_id": str(flow.event_id) if flow.event_id is not None else None,
                    "action": flow.action,
                    "description": flow.description,
                }
                for flow in flows
            ],
            "label": (
                {
                    "pattern": label[0].name,
                    "category": label[2].name,
                    "subcategory": label[1].name,
                }
                if label is not None
                else None
            ),
        }
    print(json.dumps(values, indent=2, sort_keys=True))
    return 0


def import_case(path: Path, engine: Engine | None = None) -> int:
    summary = import_incident_case(path, engine)
    action = "Updated" if summary.updated else "Imported"
    print(f"{action} case: {summary.case_id}")
    print(f"Name:          {summary.name}")
    print(f"Pattern:       {summary.attack_pattern}")
    print(f"Taxonomy:      {summary.taxonomy}")
    print(f"Attack steps:  {summary.attack_flow_steps}")
    return 0


def replay_case(case_id: uuid.UUID, engine: Engine | None = None) -> int:
    try:
        summary = replay_incident_case(case_id, engine)
    except ValueError as error:
        print(str(error))
        return 1
    print(f"Case:                 {summary.case_id}")
    print(f"Name:                 {summary.case_name}")
    print(f"Batch:                {summary.pipeline.batch_id}")
    print(f"Expected status:      {summary.expected_status}")
    print(f"Actual status:        {summary.actual_status}")
    print(f"Expected invariants:  {', '.join(sorted(summary.expected_invariants)) or '-'}")
    print(f"Actual invariants:    {', '.join(sorted(summary.actual_invariants)) or '-'}")
    print(f"Expected incidents:   {', '.join(sorted(summary.expected_incidents)) or '-'}")
    print(f"Actual incidents:     {', '.join(sorted(summary.actual_incidents)) or '-'}")
    print(f"Outcome matched:      {summary.matched}")
    return 0 if summary.matched else 1


def show_case_features(case_id: uuid.UUID, engine: Engine | None = None) -> int:
    try:
        summary = extract_case_features(case_id, engine)
    except ValueError as error:
        print(str(error))
        return 1
    print(f"Case:            {summary.case_id}")
    print(f"Name:            {summary.case_name}")
    print(f"Batch:           {summary.batch_id}")
    print(f"Replay matched:  {summary.replay_matched}")
    for feature in summary.features:
        print(f"{feature.name}: {feature.value}")
    return 0 if summary.replay_matched else 1
