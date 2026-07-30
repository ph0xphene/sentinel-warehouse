from collections.abc import Mapping
from typing import Any

from sentinel.protocols.base import ProtocolPlugin
from sentinel.protocols.erc20 import ERC20TransferPlugin
from sentinel.protocols.uniswap_v2 import UniswapV2Plugin

PLUGINS: tuple[ProtocolPlugin, ...] = (
    UniswapV2Plugin(),
    ERC20TransferPlugin(),
)


def detect_protocol(source: Mapping[str, Any]) -> ProtocolPlugin:
    matches = tuple(plugin for plugin in PLUGINS if plugin.detect(source))
    if not matches:
        raise ValueError("No protocol plugin detected for Ethereum source payload")
    if len(matches) > 1:
        names = ", ".join(plugin.name for plugin in matches)
        raise ValueError(f"Multiple protocol plugins matched: {names}")
    return matches[0]


def detect_rpc_protocol(source: Mapping[str, Any]) -> ProtocolPlugin | None:
    """Select the highest-priority plugin for a raw RPC log collection."""
    return next((plugin for plugin in PLUGINS if plugin.detect(source)), None)
