import uuid
from collections.abc import Mapping

from sqlalchemy.orm import Session

from sentinel.models import BatchStateHistory, IngestionBatch, IngestionStatus

ALLOWED_TRANSITIONS = {
    IngestionStatus.RUNNING: {IngestionStatus.STAGED, IngestionStatus.FAILED},
    IngestionStatus.STAGED: {IngestionStatus.VALIDATING, IngestionStatus.FAILED},
    IngestionStatus.VALIDATING: {IngestionStatus.LOADING, IngestionStatus.FAILED},
    IngestionStatus.LOADING: {
        IngestionStatus.INVARIANT_CHECKING,
        IngestionStatus.FAILED,
    },
    IngestionStatus.INVARIANT_CHECKING: {
        IngestionStatus.SUCCEEDED,
        IngestionStatus.FAILED,
    },
    IngestionStatus.FAILED: {IngestionStatus.RUNNING},
    IngestionStatus.SUCCEEDED: set(),
}


class InvalidBatchTransition(RuntimeError):
    pass


def record_initial_state(session: Session, batch: IngestionBatch) -> None:
    session.add(
        BatchStateHistory(
            event_id=uuid.uuid4(),
            batch_id=batch.batch_id,
            attempt_number=batch.attempt_count,
            from_status=None,
            to_status=batch.status,
            details={},
        )
    )


def transition_batch(
    session: Session,
    batch: IngestionBatch,
    target: IngestionStatus,
    details: Mapping[str, object] | None = None,
) -> None:
    current = batch.status
    if target not in ALLOWED_TRANSITIONS[current]:
        message = f"Cannot transition batch from {current.value} to {target.value}"
        raise InvalidBatchTransition(message)
    batch.status = target
    session.add(
        BatchStateHistory(
            event_id=uuid.uuid4(),
            batch_id=batch.batch_id,
            attempt_number=batch.attempt_count,
            from_status=current,
            to_status=target,
            details=dict(details or {}),
        )
    )
