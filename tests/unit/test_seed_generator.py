import json

from sentinel.ingestion import generate_fixture
from sentinel.quality import run_quality_checks


def test_generated_fixture_is_deterministic_and_reconciled(tmp_path) -> None:
    first_path = generate_fixture(tmp_path / "first.json", seed=42)
    second_path = generate_fixture(tmp_path / "second.json", seed=42)

    assert first_path.read_text() == second_path.read_text()

    fixture = json.loads(first_path.read_text())
    assert fixture["source_name"] == "synthetic_bank_42"
    assert all(result.passed for result in run_quality_checks(fixture))
