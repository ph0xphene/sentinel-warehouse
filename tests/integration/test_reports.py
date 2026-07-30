from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from sentinel.models import EventType, IncidentOrigin, IngestionStatus
from sentinel.reporting import generate_case_report, generate_incident_report
from sentinel.reporting.timeline import build_timeline
from sentinel.security import CanonicalEvent
from sentinel.security.cases import import_incident_case, replay_incident_case

pytestmark = pytest.mark.integration

CASES = Path("data/incidents")
ATTACK_CASE = CASES / "euler_style_accounting_failure.json"
CONTROL_CASE = CASES / "reconciled_transfer_control.json"
GENERATED_AT = datetime(2026, 7, 30, 18, 0, tzinfo=UTC)


def test_valid_case_produces_deterministic_offline_report(clean_engine, tmp_path) -> None:
    imported = import_incident_case(CONTROL_CASE, clean_engine)
    first_output = tmp_path / "control-first.html"
    second_output = tmp_path / "control-second.html"

    first = generate_case_report(
        imported.case_id,
        first_output,
        clean_engine,
        generated_at=GENERATED_AT,
    )
    second = generate_case_report(
        imported.case_id,
        second_output,
        clean_engine,
        generated_at=GENERATED_AT,
    )
    html = first_output.read_text()

    assert first.status == IngestionStatus.SUCCEEDED.value.upper()
    assert first.origin is IncidentOrigin.REPLAY
    assert first.events == 1
    assert first.evidence_records == 0
    assert first_output.read_bytes() == second_output.read_bytes()
    assert first.subject_id == second.subject_id
    assert "Sentinel Investigation Report" in html
    assert "Reconciled transfer control" in html
    assert "SUCCEEDED" in html
    assert "<svg" in html
    assert "<script" not in html
    assert '<link rel="stylesheet"' not in html
    assert 'src="http' not in html

    clean_engine.dispose()
    assert first_output.read_text() == html


def test_failed_invariant_and_replay_evidence_appear_in_report(
    clean_engine,
    tmp_path,
) -> None:
    imported = import_incident_case(ATTACK_CASE, clean_engine)
    output = tmp_path / "euler.html"

    summary = generate_case_report(
        "euler-style",
        output,
        clean_engine,
        generated_at=GENERATED_AT,
    )
    html = output.read_text()

    assert summary.subject_id == imported.case_id
    assert summary.status == "FAILED"
    assert summary.origin is IncidentOrigin.REPLAY
    assert summary.evidence_records == 1
    assert "balance_conservation" in html
    assert ">FAIL<" in html
    assert "unauthorized supply change" in html
    assert "REPLAY" in html
    assert str(imported.case_id) in html
    assert "EVT-EULER-ACCOUNTING" in html


def test_timeline_uses_chain_native_order_instead_of_timestamp() -> None:
    events = (
        CanonicalEvent(
            external_id="last",
            event_type=EventType.TRANSFER,
            occurred_at=datetime(2020, 1, 1, tzinfo=UTC),
            asset_external_id="TOKEN",
            account_from_external_id="A",
            account_to_external_id="B",
            amount=Decimal(1),
            metadata={},
            chain_id=1,
            block_number=100,
            transaction_index=5,
            log_index=1,
        ),
        CanonicalEvent(
            external_id="first",
            event_type=EventType.TRANSFER,
            occurred_at=datetime(2030, 1, 1, tzinfo=UTC),
            asset_external_id="TOKEN",
            account_from_external_id="B",
            account_to_external_id="A",
            amount=Decimal(1),
            metadata={},
            chain_id=1,
            block_number=99,
            transaction_index=9,
            log_index=4,
        ),
        CanonicalEvent(
            external_id="middle",
            event_type=EventType.TRANSFER,
            occurred_at=datetime(2010, 1, 1, tzinfo=UTC),
            asset_external_id="TOKEN",
            account_from_external_id="A",
            account_to_external_id="B",
            amount=Decimal(1),
            metadata={},
            chain_id=1,
            block_number=100,
            transaction_index=4,
            log_index=8,
        ),
    )

    timeline = build_timeline(events)

    assert [item.external_id for item in timeline] == ["first", "middle", "last"]
    assert timeline[1].coordinate == "Block 100 · transaction 4 · log 8"


def test_incident_report_is_self_contained(clean_engine, tmp_path) -> None:
    imported = import_incident_case(ATTACK_CASE, clean_engine)
    replay = replay_incident_case(imported.case_id, clean_engine)
    output = tmp_path / "incident.html"

    summary = generate_incident_report(
        replay.incident_ids[0],
        output,
        clean_engine,
        generated_at=GENERATED_AT,
    )
    html = output.read_text()

    assert summary.report_kind == "INCIDENT"
    assert summary.origin is IncidentOrigin.REPLAY
    assert summary.evidence_records == 1
    assert "Balance Conservation Incident" in html
    assert str(replay.incident_ids[0]) in html
    assert "<style>" in html
    assert "<svg" in html
