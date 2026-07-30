from collections import Counter, defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

COLLECTIONS = ("accounts", "assets", "transactions", "balances", "events")
REQUIRED_FIELDS = {
    "accounts": ("external_id", "name", "account_type"),
    "assets": ("external_id", "symbol", "name", "asset_type", "decimals"),
    "transactions": (
        "external_id",
        "from_account_external_id",
        "to_account_external_id",
        "asset_external_id",
        "amount",
        "occurred_at",
    ),
    "balances": (
        "external_id",
        "account_external_id",
        "asset_external_id",
        "opening_amount",
        "amount",
        "as_of",
    ),
}


@dataclass(frozen=True)
class CheckOutcome:
    check_name: str
    records_checked: int
    failures: tuple[dict[str, object], ...]

    @property
    def passed(self) -> bool:
        return not self.failures

    @property
    def failure_count(self) -> int:
        return len(self.failures)

    @property
    def details(self) -> dict[str, object]:
        return {"failures": list(self.failures)}


def _records(fixture: Mapping[str, Any], collection: str) -> list[dict[str, Any]]:
    value = fixture.get(collection, [])
    if not isinstance(value, list):
        return []
    return [record for record in value if isinstance(record, dict)]


def check_required_fields(fixture: Mapping[str, Any]) -> CheckOutcome:
    failures: list[dict[str, object]] = []
    records_checked = 1
    if not fixture.get("source_name"):
        failures.append({"collection": "fixture", "missing_fields": ["source_name"]})

    for collection, required_fields in REQUIRED_FIELDS.items():
        value = fixture.get(collection)
        if not isinstance(value, list):
            failures.append({"collection": collection, "error": "must be a list"})
            continue
        records_checked += len(value)
        for index, record in enumerate(value):
            if not isinstance(record, dict):
                failures.append(
                    {"collection": collection, "index": index, "error": "must be an object"}
                )
                continue
            missing = [field for field in required_fields if record.get(field) in (None, "")]
            if missing:
                failures.append(
                    {"collection": collection, "index": index, "missing_fields": missing}
                )

    return CheckOutcome("required_fields", records_checked, tuple(failures))


def check_duplicate_external_ids(
    fixture: Mapping[str, Any],
    existing_ids: Mapping[str, set[str]] | None = None,
) -> CheckOutcome:
    failures: list[dict[str, object]] = []
    records_checked = 0
    existing_ids = existing_ids or {}

    for collection in COLLECTIONS:
        external_ids = [
            str(record["external_id"])
            for record in _records(fixture, collection)
            if record.get("external_id") not in (None, "")
        ]
        records_checked += len(external_ids)
        counts = Counter(external_ids)
        for external_id, count in counts.items():
            if count > 1:
                failures.append(
                    {
                        "collection": collection,
                        "external_id": external_id,
                        "occurrences": count,
                        "scope": "fixture",
                    }
                )
            if external_id in existing_ids.get(collection, set()):
                failures.append(
                    {
                        "collection": collection,
                        "external_id": external_id,
                        "scope": "core",
                    }
                )

    return CheckOutcome("duplicate_external_ids", records_checked, tuple(failures))


def check_negative_amounts(fixture: Mapping[str, Any]) -> CheckOutcome:
    failures: list[dict[str, object]] = []
    transactions = _records(fixture, "transactions")

    for index, transaction in enumerate(transactions):
        try:
            amount = Decimal(str(transaction.get("amount")))
        except InvalidOperation:
            failures.append(
                {
                    "collection": "transactions",
                    "index": index,
                    "external_id": transaction.get("external_id"),
                    "error": "amount is not numeric",
                }
            )
            continue
        if amount < 0:
            failures.append(
                {
                    "collection": "transactions",
                    "index": index,
                    "external_id": transaction.get("external_id"),
                    "amount": str(amount),
                }
            )

    return CheckOutcome("negative_amounts", len(transactions), tuple(failures))


def check_transaction_reconciliation(fixture: Mapping[str, Any]) -> CheckOutcome:
    if fixture.get("events"):
        return CheckOutcome("transaction_reconciliation", 0, ())

    movements: defaultdict[tuple[str, str], Decimal] = defaultdict(Decimal)
    failures: list[dict[str, object]] = []

    for transaction in _records(fixture, "transactions"):
        try:
            asset_id = str(transaction["asset_external_id"])
            amount = Decimal(str(transaction["amount"]))
            from_key = (str(transaction["from_account_external_id"]), asset_id)
            to_key = (str(transaction["to_account_external_id"]), asset_id)
        except (KeyError, InvalidOperation):
            continue
        movements[from_key] -= amount
        movements[to_key] += amount

    balances = _records(fixture, "balances")
    for index, balance in enumerate(balances):
        try:
            key = (
                str(balance["account_external_id"]),
                str(balance["asset_external_id"]),
            )
            opening = Decimal(str(balance["opening_amount"]))
            reported = Decimal(str(balance["amount"]))
        except (KeyError, InvalidOperation):
            failures.append(
                {
                    "collection": "balances",
                    "index": index,
                    "external_id": balance.get("external_id"),
                    "error": "balance cannot be reconciled",
                }
            )
            continue
        expected = opening + movements[key]
        if expected != reported:
            failures.append(
                {
                    "collection": "balances",
                    "index": index,
                    "external_id": balance.get("external_id"),
                    "expected": str(expected),
                    "reported": str(reported),
                    "difference": str(reported - expected),
                }
            )

    return CheckOutcome("transaction_reconciliation", len(balances), tuple(failures))


def run_quality_checks(
    fixture: Mapping[str, Any],
    existing_ids: Mapping[str, set[str]] | None = None,
    enabled_checks: tuple[str, ...] | None = None,
) -> tuple[CheckOutcome, ...]:
    checks = {
        "required_fields": lambda: check_required_fields(fixture),
        "duplicate_external_ids": lambda: check_duplicate_external_ids(fixture, existing_ids),
        "negative_amounts": lambda: check_negative_amounts(fixture),
        "transaction_reconciliation": lambda: check_transaction_reconciliation(fixture),
    }
    selected = tuple(checks) if enabled_checks is None else enabled_checks
    return tuple(checks[name]() for name in selected)
