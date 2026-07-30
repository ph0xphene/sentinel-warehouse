import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy.orm import Session

from sentinel.ingestion.lifecycle import InvalidBatchTransition, transition_batch
from sentinel.models import IngestionBatch, IngestionStatus


def test_terminal_success_state_cannot_transition() -> None:
    batch = IngestionBatch(
        batch_id=uuid.uuid4(),
        source_name="test",
        started_at=datetime.now(UTC),
        status=IngestionStatus.SUCCEEDED,
        rows_loaded=1,
        checksum="checksum",
    )

    with pytest.raises(InvalidBatchTransition):
        transition_batch(Session(), batch, IngestionStatus.RUNNING)
