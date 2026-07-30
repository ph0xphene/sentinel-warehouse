import json

import pytest

from sentinel.quality import QualityConfig


def test_quality_config_controls_enabled_and_blocking_checks(tmp_path) -> None:
    path = tmp_path / "quality.json"
    path.write_text(
        json.dumps(
            {
                "checks": {
                    "required_fields": {"enabled": True, "blocking": True},
                    "negative_amounts": {"enabled": False, "blocking": True},
                    "transaction_reconciliation": {
                        "enabled": True,
                        "blocking": False,
                    },
                }
            }
        )
    )

    config = QualityConfig.from_file(path)

    assert config.enabled_checks == ("required_fields", "transaction_reconciliation")
    assert config.is_blocking("required_fields")
    assert not config.is_blocking("transaction_reconciliation")


def test_quality_config_rejects_unknown_check(tmp_path) -> None:
    path = tmp_path / "quality.json"
    path.write_text(json.dumps({"checks": {"imaginary_check": {"enabled": True}}}))

    with pytest.raises(ValueError, match="imaginary_check"):
        QualityConfig.from_file(path)
