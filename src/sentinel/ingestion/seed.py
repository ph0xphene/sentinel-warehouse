import json
import random
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path


def generate_fixture(output_path: Path, seed: int = 7) -> Path:
    """Generate a deterministic, reconciled financial fixture."""
    randomizer = random.Random(seed)
    base_time = datetime(2026, 1, 1, tzinfo=UTC)
    opening_operating = Decimal("10000.00")
    opening_reserve = Decimal("5000.00")
    transfer_out = Decimal(randomizer.randrange(100_00, 2000_00)) / 100
    transfer_back = Decimal(randomizer.randrange(50_00, 500_00)) / 100

    fixture = {
        "source_name": f"synthetic_bank_{seed}",
        "checkpoint": (base_time + timedelta(days=1)).isoformat(),
        "accounts": [
            {"external_id": "ACC-100", "name": "Operating", "account_type": "checking"},
            {"external_id": "ACC-200", "name": "Reserve", "account_type": "savings"},
        ],
        "assets": [
            {
                "external_id": "USD",
                "symbol": "USD",
                "name": "US Dollar",
                "asset_type": "fiat",
                "decimals": 2,
            }
        ],
        "transactions": [
            {
                "external_id": "TX-1001",
                "from_account_external_id": "ACC-100",
                "to_account_external_id": "ACC-200",
                "asset_external_id": "USD",
                "amount": str(transfer_out),
                "occurred_at": (base_time + timedelta(hours=1)).isoformat(),
                "description": "Operating reserve transfer",
            },
            {
                "external_id": "TX-1002",
                "from_account_external_id": "ACC-200",
                "to_account_external_id": "ACC-100",
                "asset_external_id": "USD",
                "amount": str(transfer_back),
                "occurred_at": (base_time + timedelta(hours=2)).isoformat(),
                "description": "Reserve adjustment",
            },
        ],
        "balances": [
            {
                "external_id": "BAL-100-20260101",
                "account_external_id": "ACC-100",
                "asset_external_id": "USD",
                "opening_amount": str(opening_operating),
                "amount": str(opening_operating - transfer_out + transfer_back),
                "as_of": (base_time + timedelta(days=1)).isoformat(),
            },
            {
                "external_id": "BAL-200-20260101",
                "account_external_id": "ACC-200",
                "asset_external_id": "USD",
                "opening_amount": str(opening_reserve),
                "amount": str(opening_reserve + transfer_out - transfer_back),
                "as_of": (base_time + timedelta(days=1)).isoformat(),
            },
        ],
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(fixture, indent=2) + "\n", encoding="utf-8")
    return output_path
