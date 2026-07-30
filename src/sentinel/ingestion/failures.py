import enum
from dataclasses import dataclass, field


class FailurePoint(enum.StrEnum):
    AFTER_RAW_STAGE = "after_raw_stage"
    BEFORE_VALIDATION = "before_validation"
    AFTER_VALIDATION = "after_validation"
    BEFORE_CORE_LOAD = "before_core_load"
    AFTER_CORE_LOAD = "after_core_load"
    BEFORE_INVARIANT_CHECK = "before_invariant_check"
    AFTER_INVARIANT_CHECK = "after_invariant_check"


class InjectedPipelineFailure(RuntimeError):
    def __init__(self, point: FailurePoint) -> None:
        self.point = point
        super().__init__(f"Injected pipeline failure at {point.value}")


@dataclass
class FailureInjector:
    """Deterministically inject failures at named pipeline boundaries."""

    fail_at: frozenset[FailurePoint] = field(default_factory=frozenset)

    def trigger(self, point: FailurePoint) -> None:
        if point in self.fail_at:
            raise InjectedPipelineFailure(point)
