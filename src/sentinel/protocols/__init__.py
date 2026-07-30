"""Protocol plugin contract and built-in registry."""

from sentinel.protocols.base import (
    ProtocolNormalization,
    ProtocolPlugin,
    ProtocolRawRecord,
)
from sentinel.protocols.registry import detect_protocol, detect_rpc_protocol

__all__ = [
    "ProtocolNormalization",
    "ProtocolPlugin",
    "ProtocolRawRecord",
    "detect_protocol",
    "detect_rpc_protocol",
]
