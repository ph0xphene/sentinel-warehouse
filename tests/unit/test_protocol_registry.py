import pytest

from sentinel.protocols import detect_protocol


def test_registry_detects_uniswap_before_generic_erc20() -> None:
    plugin = detect_protocol(
        {
            "protocol": "uniswap_v2",
            "protocol_events": [{"event_name": "Sync"}],
            "transfers": [],
        }
    )

    assert plugin.name == "uniswap_v2"


def test_registry_rejects_unknown_protocol() -> None:
    with pytest.raises(ValueError, match="No protocol plugin"):
        detect_protocol({"protocol": "unknown", "protocol_events": []})
