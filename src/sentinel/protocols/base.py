from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from sentinel.security import CanonicalEvent, InvariantOutcome


@dataclass(frozen=True)
class ProtocolRawRecord:
    record_type: str
    external_id: str
    payload: dict[str, object]


@dataclass(frozen=True)
class ProtocolNormalization:
    events: tuple[dict[str, Any], ...]
    account_addresses: frozenset[str]
    raw_records: tuple[ProtocolRawRecord, ...]
    transfers: tuple[dict[str, Any], ...] = ()
    asset_definitions: tuple[dict[str, Any], ...] = ()


class ProtocolPlugin(Protocol):
    name: str

    def detect(self, source: Mapping[str, Any]) -> bool:
        """Return whether this plugin owns the source payload."""

    def normalize(self, source: Mapping[str, Any]) -> ProtocolNormalization:
        """Convert protocol source records into canonical event dictionaries."""

    def invariants(
        self,
        events: tuple[CanonicalEvent, ...],
        source: Mapping[str, Any],
    ) -> tuple[InvariantOutcome, ...]:
        """Run protocol-specific invariants over canonical event state."""
