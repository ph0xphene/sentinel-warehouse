import pytest

from sentinel.protocols import detect_protocol
from sentinel.protocols.uniswap_v2 import UniswapV2Plugin


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


def test_uniswap_unknown_token_metadata_is_not_fabricated() -> None:
    normalization = UniswapV2Plugin().normalize(
        {
            "protocol": "uniswap_v2",
            "chain_id": 1,
            "pair": {
                "address": "0x9999999999999999999999999999999999999999",
                "token0": None,
                "token1": None,
            },
            "tokens": [],
            "transactions": [
                {
                    "tx_hash": "0x" + "ab" * 32,
                    "block_number": 100,
                    "transaction_index": 2,
                    "block_timestamp": "2026-01-01T00:00:00Z",
                    "success": True,
                }
            ],
            "protocol_events": [
                {
                    "event_name": "Sync",
                    "tx_hash": "0x" + "ab" * 32,
                    "log_index": 4,
                    "reserve0": "100",
                    "reserve1": "200",
                }
            ],
        }
    )

    assert normalization.asset_definitions == ()
    assert len(normalization.events) == 1
    assert normalization.events[0]["asset_external_id"] is None
    assert normalization.events[0]["metadata"]["asset_metadata_status"] == "unknown"
