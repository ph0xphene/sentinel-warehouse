from pathlib import Path

import pytest
from sqlalchemy import func, select

from sentinel.cli.cases import list_cases, replay_case, show_case
from sentinel.cli.incidents import list_incidents
from sentinel.models import (
    AttackCategory,
    AttackFlow,
    AttackSubcategory,
    FinancialEvent,
    Incident,
    IncidentCase,
    IncidentOrigin,
    IngestionStatus,
)
from sentinel.security.cases import import_incident_case, replay_incident_case

pytestmark = pytest.mark.integration

CASES = Path("data/incidents")
ATTACK_CASE = CASES / "euler_style_accounting_failure.json"
CONTROL_CASE = CASES / "reconciled_transfer_control.json"


def test_case_import_is_idempotent_and_attack_flow_is_ordered(clean_engine) -> None:
    imported = import_incident_case(ATTACK_CASE, clean_engine)
    updated = import_incident_case(ATTACK_CASE, clean_engine)

    with clean_engine.connect() as connection:
        case = connection.execute(
            select(
                IncidentCase.name,
                IncidentCase.protocol,
                IncidentCase.chain,
                IncidentCase.category,
                IncidentCase.confidence_level,
                IncidentCase.affected_contracts,
                IncidentCase.attacker_addresses,
                IncidentCase.reference_transactions,
                IncidentCase.external_references,
            )
        ).one()
        flows = connection.execute(
            select(AttackFlow.step_number, AttackFlow.action).order_by(AttackFlow.step_number)
        ).all()
        case_count = connection.scalar(select(func.count()).select_from(IncidentCase))
        taxonomy = connection.execute(
            select(AttackCategory.name, AttackSubcategory.name).join(
                AttackSubcategory,
                AttackCategory.category_id == AttackSubcategory.category_id,
            )
        ).one()

    assert not imported.updated
    assert updated.updated
    assert imported.case_id == updated.case_id
    assert imported.attack_flow_steps == 3
    assert case.name == "Euler-style accounting failure"
    assert case.protocol == "generic_financial"
    assert case.chain == "research-simulation"
    assert case.category == "accounting"
    assert case.confidence_level == "high"
    assert case.affected_contracts == ["research:vault"]
    assert case.attacker_addresses == ["research:attacker"]
    assert len(case.reference_transactions) == 1
    assert len(case.external_references) == 1
    assert taxonomy == ("Financial state manipulation", "Unauthorized supply change")
    assert flows == [
        (1, "establish_state"),
        (2, "manipulate_accounting"),
        (3, "detect_violation"),
    ]
    assert case_count == 1


def test_case_replay_creates_and_matches_expected_incident(clean_engine) -> None:
    imported = import_incident_case(ATTACK_CASE, clean_engine)

    replay = replay_incident_case(imported.case_id, clean_engine)

    with clean_engine.connect() as connection:
        incident = connection.execute(
            select(
                Incident.incident_id,
                Incident.incident_type,
                Incident.severity,
                Incident.origin,
                Incident.case_id,
            ).where(Incident.batch_id == replay.pipeline.batch_id)
        ).one()
        event_count = connection.scalar(select(func.count()).select_from(FinancialEvent))

    assert replay.pipeline.status is IngestionStatus.FAILED
    assert replay.actual_invariants == frozenset({"balance_conservation"})
    assert replay.actual_incidents == frozenset({"balance_conservation"})
    assert replay.incident_ids == (incident.incident_id,)
    assert replay.matched
    assert incident.incident_type == "balance_conservation"
    assert incident.severity == "critical"
    assert incident.origin is IncidentOrigin.REPLAY
    assert incident.case_id == imported.case_id
    assert event_count == 0


def test_control_case_replay_has_no_false_positives_and_links_event(clean_engine) -> None:
    imported = import_incident_case(CONTROL_CASE, clean_engine)

    replay = replay_incident_case(imported.case_id, clean_engine)

    with clean_engine.connect() as connection:
        incident_count = connection.scalar(select(func.count()).select_from(Incident))
        linked_event = connection.scalar(
            select(AttackFlow.event_id).where(AttackFlow.step_number == 2)
        )
        event_id = connection.scalar(
            select(FinancialEvent.event_id).where(FinancialEvent.external_id == "TX-CONTROL")
        )

    assert replay.pipeline.status is IngestionStatus.SUCCEEDED
    assert replay.actual_invariants == frozenset()
    assert replay.actual_incidents == frozenset()
    assert replay.matched
    assert incident_count == 0
    assert linked_event == event_id


def test_case_cli_lists_shows_and_replays_research_case(clean_engine, capsys) -> None:
    imported = import_incident_case(ATTACK_CASE, clean_engine)

    assert list_cases(clean_engine) == 0
    list_output = capsys.readouterr().out
    assert str(imported.case_id) in list_output
    assert "Euler-style accounting failure" in list_output

    assert show_case(imported.case_id, clean_engine) == 0
    show_output = capsys.readouterr().out
    assert '"step_number": 1' in show_output
    assert '"expected"' in show_output

    assert replay_case(imported.case_id, clean_engine) == 0
    replay_output = capsys.readouterr().out
    assert "Outcome matched:      True" in replay_output
    assert "balance_conservation" in replay_output


def test_replay_incident_is_excluded_from_live_incident_list(clean_engine, capsys) -> None:
    imported = import_incident_case(ATTACK_CASE, clean_engine)
    replay = replay_incident_case(imported.case_id, clean_engine)

    assert list_incidents(clean_engine, origin=IncidentOrigin.LIVE) == 0
    assert capsys.readouterr().out == "No incidents found.\n"

    assert list_incidents(clean_engine, origin=IncidentOrigin.REPLAY) == 0
    replay_output = capsys.readouterr().out
    assert str(replay.incident_ids[0]) in replay_output
    assert "REPLAY" in replay_output
