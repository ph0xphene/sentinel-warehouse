import json
from pathlib import Path

import pytest
from sqlalchemy import func, select

from sentinel.ingestion import ingest_ethereum_fixture
from sentinel.models import (
    FinancialEvent,
    Incident,
    IncidentEvidence,
    IngestionStatus,
    InvariantResult,
    RawFinancialRecord,
)
from sentinel.protocols import detect_protocol

pytestmark = pytest.mark.integration

FIXTURES = Path("data/fixtures")


def test_uniswap_plugin_detects_and_normalizes_all_supported_events(clean_engine) -> None:
    source = json.loads((FIXTURES / "uniswap_v2_valid.json").read_text())
    plugin = detect_protocol(source)

    assert plugin.name == "uniswap_v2"
    assert plugin.detect(source)

    summary = ingest_ethereum_fixture(FIXTURES / "uniswap_v2_valid.json", clean_engine)

    with clean_engine.connect() as connection:
        event_types = set(
            connection.scalars(
                select(FinancialEvent.event_type).where(FinancialEvent.source_system == "ethereum")
            )
        )
        protocol_events = set(
            connection.scalars(
                select(FinancialEvent.event_metadata["protocol_event"].as_string()).where(
                    FinancialEvent.event_metadata["protocol"].as_string() == "uniswap_v2"
                )
            )
        )
        raw_protocol_records = connection.scalar(
            select(func.count())
            .select_from(RawFinancialRecord)
            .where(RawFinancialRecord.source_name == "ethereum")
        )
        protocol_invariants = connection.execute(
            select(
                InvariantResult.name,
                InvariantResult.execution_result,
                InvariantResult.protocol_name,
            ).where(InvariantResult.protocol_name == "uniswap_v2")
        ).all()
        incident_count = connection.scalar(select(func.count()).select_from(Incident))

    assert summary.status is IngestionStatus.SUCCEEDED
    assert summary.raw_records == 12
    assert {"MINT", "BURN", "DEPOSIT", "WITHDRAWAL", "ADJUSTMENT"} <= event_types
    assert {"Mint", "Burn", "Swap", "Sync"} == protocol_events
    assert raw_protocol_records == 6
    assert {row.name for row in protocol_invariants} == {
        "reserve_consistency",
        "liquidity_conservation",
    }
    assert all(row.execution_result == "passed" for row in protocol_invariants)
    assert incident_count == 0


def test_uniswap_reserve_violation_creates_protocol_incident(clean_engine) -> None:
    summary = ingest_ethereum_fixture(
        FIXTURES / "uniswap_v2_reserve_mismatch.json",
        clean_engine,
    )
    invariants = {result.name: result for result in summary.invariant_results}

    with clean_engine.connect() as connection:
        incident = connection.execute(
            select(
                Incident.incident_id,
                Incident.incident_type,
                Incident.protocol_name,
            ).where(Incident.batch_id == summary.batch_id)
        ).one()
        evidence_protocol = connection.scalar(
            select(IncidentEvidence.payload["invariant"].as_string()).where(
                IncidentEvidence.incident_id == incident.incident_id
            )
        )
        event_count = connection.scalar(select(func.count()).select_from(FinancialEvent))

    assert summary.status is IngestionStatus.FAILED
    assert not invariants["reserve_consistency"].passed
    assert invariants["liquidity_conservation"].passed
    assert incident.incident_type == "reserve_consistency"
    assert incident.protocol_name == "uniswap_v2"
    assert evidence_protocol == "reserve_consistency"
    assert event_count == 0
