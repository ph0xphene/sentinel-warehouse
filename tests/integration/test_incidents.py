from pathlib import Path

import pytest
from sqlalchemy import func, select

import sentinel.ingestion.fixture as fixture_pipeline
from sentinel.cli.incidents import list_incidents, show_incident
from sentinel.ingestion import ingest_fixture
from sentinel.models import (
    Incident,
    IncidentEvidence,
    IncidentOrigin,
    IncidentStatus,
    IngestionStatus,
    InvariantResult,
)
from sentinel.security import InvariantOutcome

pytestmark = pytest.mark.integration

FIXTURES = Path("data/fixtures")


def test_invariant_failure_creates_incident_with_evidence(clean_engine) -> None:
    summary = ingest_fixture(FIXTURES / "event_missing_destination.json", clean_engine)

    with clean_engine.connect() as connection:
        incident = connection.execute(
            select(
                Incident.incident_id,
                Incident.status,
                Incident.incident_type,
                Incident.origin,
            ).where(
                Incident.batch_id == summary.batch_id,
                Incident.incident_type == "event_completeness",
            )
        ).one()
        evidence = connection.execute(
            select(
                IncidentEvidence.affected_entity,
                IncidentEvidence.evidence_type,
                IncidentEvidence.payload,
                IncidentEvidence.origin,
            ).where(IncidentEvidence.incident_id == incident.incident_id)
        ).one()
        invariant_origin = connection.scalar(
            select(InvariantResult.origin).where(
                InvariantResult.batch_id == summary.batch_id,
                InvariantResult.name == "event_completeness",
            )
        )

    assert summary.status is IngestionStatus.FAILED
    assert incident.status is IncidentStatus.OPEN
    assert incident.incident_type == "event_completeness"
    assert incident.origin is IncidentOrigin.FIXTURE
    assert invariant_origin is IncidentOrigin.FIXTURE
    assert evidence.affected_entity == "EVT-MISSING-DESTINATION"
    assert evidence.evidence_type == "invariant_violation"
    assert evidence.payload["invariant"] == "event_completeness"
    assert evidence.origin is IncidentOrigin.FIXTURE


def test_successful_batch_creates_no_incidents(clean_engine) -> None:
    summary = ingest_fixture(FIXTURES / "synthetic_financial.json", clean_engine)

    with clean_engine.connect() as connection:
        incident_count = connection.scalar(select(func.count()).select_from(Incident))

    assert summary.status is IngestionStatus.SUCCEEDED
    assert incident_count == 0


def test_successful_retry_resolves_previous_incident(clean_engine, monkeypatch) -> None:
    actual_run_invariants = fixture_pipeline.run_invariants

    def force_transient_violation(events, reported_balances, context):
        outcomes = list(actual_run_invariants(events, reported_balances, context))
        outcomes[0] = InvariantOutcome(
            name="balance_conservation",
            severity="critical",
            description="Injected transient conservation failure.",
            affected_records=(
                {
                    "external_id": "TRANSIENT-EVENT",
                    "reason": "transient test condition",
                },
            ),
        )
        return tuple(outcomes)

    monkeypatch.setattr(fixture_pipeline, "run_invariants", force_transient_violation)
    failed = ingest_fixture(FIXTURES / "synthetic_financial.json", clean_engine)
    monkeypatch.setattr(fixture_pipeline, "run_invariants", actual_run_invariants)
    retried = ingest_fixture(FIXTURES / "synthetic_financial.json", clean_engine)

    with clean_engine.connect() as connection:
        incident_status = connection.scalar(
            select(Incident.status).where(Incident.batch_id == retried.batch_id)
        )

    assert failed.status is IngestionStatus.FAILED
    assert retried.status is IngestionStatus.SUCCEEDED
    assert retried.batch_id == failed.batch_id
    assert retried.attempt_number == 2
    assert incident_status is IncidentStatus.RESOLVED


def test_incident_cli_lists_and_shows_evidence(clean_engine, capsys) -> None:
    summary = ingest_fixture(FIXTURES / "event_create_money.json", clean_engine)
    with clean_engine.connect() as connection:
        incident_id = connection.scalar(
            select(Incident.incident_id).where(Incident.batch_id == summary.batch_id)
        )

    assert incident_id is not None
    assert list_incidents(clean_engine) == 0
    list_output = capsys.readouterr().out
    assert str(incident_id) in list_output
    assert "OPEN" in list_output

    assert show_incident(incident_id, clean_engine) == 0
    show_output = capsys.readouterr().out
    assert '"evidence_type": "invariant_violation"' in show_output
    assert '"affected_entity": "EVT-CREATE-MONEY"' in show_output
