from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pyarrow.parquet as pq
import pytest
from sqlalchemy import func, select, update

import sentinel.security.dataset as dataset_module
from sentinel.cli.cases import show_case_features
from sentinel.cli.datasets import export_dataset, validate_dataset
from sentinel.models import AttackPattern, IncidentCase, IncidentFeature
from sentinel.security.cases import import_incident_case
from sentinel.security.dataset import (
    DATASET_VERSION,
    SCHEMA_VERSION,
    export_incident_dataset,
    validate_incident_dataset,
)
from sentinel.security.features import EXTRACTION_VERSION, extract_case_features

pytestmark = pytest.mark.integration

CASES = Path("data/incidents")
ATTACK_CASE = CASES / "euler_style_accounting_failure.json"
CONTROL_CASE = CASES / "reconciled_transfer_control.json"


def _values(summary) -> dict[str, Decimal | str]:
    return {feature.name: feature.value for feature in summary.features}


def test_feature_extraction_is_deterministic_and_replaces_stored_values(clean_engine) -> None:
    imported = import_incident_case(ATTACK_CASE, clean_engine)

    first = extract_case_features(imported.case_id, clean_engine)
    second = extract_case_features(imported.case_id, clean_engine)

    with clean_engine.connect() as connection:
        stored_count = connection.scalar(select(func.count()).select_from(IncidentFeature))
        stored = connection.execute(
            select(
                IncidentFeature.feature_name,
                IncidentFeature.numeric_value,
                IncidentFeature.categorical_value,
            ).order_by(IncidentFeature.feature_name)
        ).all()

    assert first.features == second.features
    assert first.batch_id == second.batch_id
    assert first.replay_matched
    assert stored_count == 13
    assert len(stored) == 13
    assert _values(first) == {
        "affected_assets": "USD",
        "asset_transition_graph_size": Decimal(1),
        "balance_delta": Decimal("100.00"),
        "event_sequence_length": Decimal(3),
        "event_type_sequence": "ADJUSTMENT",
        "failed_invariants": "balance_conservation",
        "invariant_failure_category": "accounting_integrity",
        "number_of_accounts": Decimal(2),
        "number_of_events": Decimal(1),
        "protocol_type": "generic_financial",
        "time_window_duration": Decimal(0),
        "transferred_volume": Decimal("100.00"),
        "unique_contracts_count": Decimal(1),
    }


def test_control_case_has_a_distinct_feature_and_label_profile(clean_engine) -> None:
    attack = import_incident_case(ATTACK_CASE, clean_engine)
    control = import_incident_case(CONTROL_CASE, clean_engine)

    attack_values = _values(extract_case_features(attack.case_id, clean_engine))
    control_values = _values(extract_case_features(control.case_id, clean_engine))

    with clean_engine.connect() as connection:
        patterns = set(connection.execute(select(AttackPattern.name, AttackPattern.category)).all())

    assert attack_values["balance_delta"] == Decimal("100.00")
    assert control_values["balance_delta"] == Decimal("0")
    assert attack_values["failed_invariants"] == "balance_conservation"
    assert control_values["failed_invariants"] == "none"
    assert patterns == {
        ("Unbacked balance creation", "accounting"),
        ("Benign conserved transfer", "control"),
    }


def test_dataset_export_is_wide_labeled_parquet(clean_engine, tmp_path) -> None:
    import_incident_case(ATTACK_CASE, clean_engine)
    import_incident_case(CONTROL_CASE, clean_engine)
    output = tmp_path / "security-incidents.parquet"

    summary = export_incident_dataset(output, clean_engine)
    table = pq.read_table(output)
    rows = sorted(table.to_pylist(), key=lambda row: row["case_name"])

    assert summary.output == output.resolve()
    assert summary.rows == 2
    assert summary.columns == 36
    assert table.schema.metadata[b"dataset"] == b"sentinel_security_incidents"
    assert table.schema.metadata[b"dataset_version"] == DATASET_VERSION.encode()
    assert table.schema.metadata[b"extraction_version"] == EXTRACTION_VERSION.encode()
    assert table.schema.metadata[b"extractor_version"] == EXTRACTION_VERSION.encode()
    assert table.schema.metadata[b"schema_version"] == SCHEMA_VERSION.encode()
    assert table.schema.metadata[b"generated_at"].endswith(b"Z")
    assert table.schema.metadata[b"git_revision"]
    assert summary.git_revision == table.schema.metadata[b"git_revision"].decode()
    assert all(
        table.schema.field(name).type == dataset_module.pa.decimal128(38, 18)
        for name in dataset_module.NUMERIC_FEATURES
    )
    assert {row["attack_pattern_name"] for row in rows} == {
        "Unbacked balance creation",
        "Benign conserved transfer",
    }
    attack_row = next(row for row in rows if row["category"] == "accounting")
    control_row = next(row for row in rows if row["category"] == "control")
    assert attack_row["failed_invariants"] == "balance_conservation"
    assert attack_row["event_type_sequence"] == "ADJUSTMENT"
    assert attack_row["invariant_failure_category"] == "accounting_integrity"
    assert attack_row["attack_category_name"] == "Financial state manipulation"
    assert attack_row["attack_subcategory_name"] == "Unauthorized supply change"
    assert attack_row["chain"] == "research-simulation"
    assert attack_row["confidence_level"] == "high"
    assert isinstance(attack_row["balance_delta"], Decimal)
    assert attack_row["balance_delta"] == Decimal("100.000000000000000000")
    assert control_row["failed_invariants"] == "none"
    assert control_row["event_type_sequence"] == "TRANSFER"
    assert isinstance(control_row["balance_delta"], Decimal)
    assert control_row["balance_delta"] == Decimal("0E-18")
    assert all(row["replay_matched"] for row in rows)


def test_feature_and_dataset_cli_commands(clean_engine, tmp_path, capsys) -> None:
    attack = import_incident_case(ATTACK_CASE, clean_engine)
    output = tmp_path / "cli-export.parquet"

    assert show_case_features(attack.case_id, clean_engine) == 0
    feature_output = capsys.readouterr().out
    assert "balance_delta: 100.00" in feature_output
    assert "failed_invariants: balance_conservation" in feature_output

    assert export_dataset(output, clean_engine) == 0
    export_output = capsys.readouterr().out
    assert str(output.resolve()) in export_output
    assert "Rows:     1" in export_output
    assert output.exists()

    assert validate_dataset(clean_engine) == 0
    validation_output = capsys.readouterr().out
    assert "Valid:         True" in validation_output


def test_dataset_validation_rejects_missing_label_and_provenance(clean_engine) -> None:
    imported = import_incident_case(ATTACK_CASE, clean_engine)
    with clean_engine.begin() as connection:
        connection.execute(
            update(IncidentCase)
            .where(IncidentCase.case_id == imported.case_id)
            .values(attack_pattern_id=None, chain=None)
        )

    report = validate_incident_dataset(clean_engine)

    assert not report.valid
    assert any("missing attack-pattern label" in issue for issue in report.issues)
    assert any("missing provenance chain" in issue for issue in report.issues)


def test_dataset_validation_rejects_replay_mismatch(clean_engine) -> None:
    imported = import_incident_case(ATTACK_CASE, clean_engine)
    with clean_engine.begin() as connection:
        replay = dict(
            connection.scalar(
                select(IncidentCase.replay_definition).where(
                    IncidentCase.case_id == imported.case_id
                )
            )
        )
        replay["expected"] = {
            "status": "succeeded",
            "invariants": [],
            "incidents": [],
        }
        connection.execute(
            update(IncidentCase)
            .where(IncidentCase.case_id == imported.case_id)
            .values(replay_definition=replay)
        )

    report = validate_incident_dataset(clean_engine)

    assert not report.valid
    assert any("replay does not match expected outcome" in issue for issue in report.issues)


def test_dataset_validation_detects_non_deterministic_features(
    clean_engine,
    monkeypatch,
) -> None:
    import_incident_case(CONTROL_CASE, clean_engine)
    actual_extract = dataset_module.extract_case_features
    calls = 0

    def alternating_extract(case_id, engine):
        nonlocal calls
        calls += 1
        summary = actual_extract(case_id, engine)
        if calls % 2 == 0:
            return replace(summary, features=tuple(reversed(summary.features)))
        return summary

    monkeypatch.setattr(dataset_module, "extract_case_features", alternating_extract)
    report = dataset_module.validate_incident_dataset(clean_engine)

    assert not report.valid
    assert any("non-deterministic" in issue for issue in report.issues)
