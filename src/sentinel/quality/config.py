import json
from dataclasses import dataclass
from pathlib import Path

DEFAULT_CHECKS = (
    "required_fields",
    "duplicate_external_ids",
    "negative_amounts",
    "transaction_reconciliation",
)


@dataclass(frozen=True)
class CheckPolicy:
    enabled: bool = True
    blocking: bool = True


@dataclass(frozen=True)
class QualityConfig:
    checks: dict[str, CheckPolicy]

    @classmethod
    def default(cls) -> "QualityConfig":
        return cls({name: CheckPolicy() for name in DEFAULT_CHECKS})

    @classmethod
    def from_file(cls, path: Path) -> "QualityConfig":
        raw = json.loads(path.read_text(encoding="utf-8"))
        configured = raw.get("checks", {})
        checks = {
            name: CheckPolicy(
                enabled=bool(policy.get("enabled", True)),
                blocking=bool(policy.get("blocking", True)),
            )
            for name, policy in configured.items()
        }
        unknown = set(checks) - set(DEFAULT_CHECKS)
        if unknown:
            raise ValueError(f"Unknown quality checks: {', '.join(sorted(unknown))}")
        return cls(checks)

    @property
    def enabled_checks(self) -> tuple[str, ...]:
        return tuple(name for name, policy in self.checks.items() if policy.enabled)

    def is_blocking(self, check_name: str) -> bool:
        policy = self.checks.get(check_name)
        return policy.blocking if policy is not None else True
