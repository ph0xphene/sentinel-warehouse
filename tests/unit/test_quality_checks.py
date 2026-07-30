import json
from copy import deepcopy
from pathlib import Path

from sentinel.quality.checks import run_quality_checks

FIXTURE_PATH = Path("data/fixtures/synthetic_financial.json")


def _fixture() -> dict:
    return json.loads(FIXTURE_PATH.read_text())


def test_valid_fixture_passes_all_checks() -> None:
    outcomes = run_quality_checks(_fixture())

    assert all(outcome.passed for outcome in outcomes)


def test_required_fields_reports_missing_value() -> None:
    fixture = _fixture()
    fixture["accounts"][0].pop("name")

    outcomes = {result.check_name: result for result in run_quality_checks(fixture)}

    assert not outcomes["required_fields"].passed
    assert outcomes["required_fields"].failure_count == 1


def test_duplicate_external_ids_are_detected() -> None:
    fixture = _fixture()
    fixture["transactions"].append(deepcopy(fixture["transactions"][0]))

    outcomes = {result.check_name: result for result in run_quality_checks(fixture)}

    assert not outcomes["duplicate_external_ids"].passed


def test_negative_amount_is_detected() -> None:
    fixture = _fixture()
    fixture["transactions"][0]["amount"] = "-1.00"

    outcomes = {result.check_name: result for result in run_quality_checks(fixture)}

    assert not outcomes["negative_amounts"].passed


def test_unreconciled_balance_is_detected() -> None:
    fixture = _fixture()
    fixture["balances"][0]["amount"] = "8999.99"

    outcomes = {result.check_name: result for result in run_quality_checks(fixture)}

    assert not outcomes["transaction_reconciliation"].passed
