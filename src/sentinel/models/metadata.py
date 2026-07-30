import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from sentinel.models.base import Base
from sentinel.models.enums import AnalysisStatus, IncidentOrigin


class IngestionStatus(enum.StrEnum):
    RUNNING = "running"
    STAGED = "staged"
    VALIDATING = "validating"
    LOADING = "loading"
    INVARIANT_CHECKING = "invariant_checking"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class IngestionBatch(Base):
    """One source ingestion attempt and its operational lineage."""

    __tablename__ = "ingestion_batches"
    __table_args__ = (
        UniqueConstraint(
            "source_name",
            "checksum",
            name="uq_ingestion_batches_source_checksum",
        ),
        {"schema": "metadata"},
    )

    batch_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    source_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[IngestionStatus] = mapped_column(
        Enum(
            IngestionStatus,
            name="ingestion_status",
            schema="metadata",
            values_callable=lambda enum: [member.value for member in enum],
        ),
        nullable=False,
    )
    rows_loaded: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    checksum: Mapped[str | None] = mapped_column(Text)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    analysis_status: Mapped[AnalysisStatus] = mapped_column(
        Enum(
            AnalysisStatus,
            name="analysis_status",
            schema="metadata",
            values_callable=lambda enum: [member.value for member in enum],
        ),
        nullable=False,
        default=AnalysisStatus.SUPPORTED,
    )
    origin: Mapped[IncidentOrigin] = mapped_column(
        Enum(
            IncidentOrigin,
            name="incident_origin",
            schema="security",
            values_callable=lambda enum: [member.value for member in enum],
        ),
        nullable=False,
        default=IncidentOrigin.FIXTURE,
    )
    case_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("security.incident_cases.case_id"),
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class QualityResult(Base):
    """Persisted outcome of one quality check for an ingestion batch."""

    __tablename__ = "quality_results"
    __table_args__ = {"schema": "metadata"}

    result_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    batch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("metadata.ingestion_batches.batch_id"),
        nullable=False,
        index=True,
    )
    check_name: Mapped[str] = mapped_column(String(100), nullable=False)
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    passed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    records_checked: Mapped[int] = mapped_column(BigInteger, nullable=False)
    failure_count: Mapped[int] = mapped_column(BigInteger, nullable=False)
    details: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class SourceCheckpoint(Base):
    """Latest successfully committed position for one source."""

    __tablename__ = "source_checkpoints"
    __table_args__ = {"schema": "metadata"}

    checkpoint_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    source_name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    checkpoint_value: Mapped[str] = mapped_column(Text, nullable=False)
    chain_id: Mapped[int | None] = mapped_column(BigInteger)
    source_identity: Mapped[str | None] = mapped_column(String(255))
    block_number: Mapped[int | None] = mapped_column(BigInteger)
    block_hash: Mapped[str | None] = mapped_column(String(66))
    last_batch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("metadata.ingestion_batches.batch_id"),
        nullable=False,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class BatchStateHistory(Base):
    """Audit record for a batch lifecycle transition."""

    __tablename__ = "batch_state_history"
    __table_args__ = {"schema": "metadata"}

    event_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    batch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("metadata.ingestion_batches.batch_id"),
        nullable=False,
        index=True,
    )
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    from_status: Mapped[IngestionStatus | None] = mapped_column(
        Enum(
            IngestionStatus,
            name="ingestion_status",
            schema="metadata",
            values_callable=lambda enum: [member.value for member in enum],
            create_type=False,
        )
    )
    to_status: Mapped[IngestionStatus] = mapped_column(
        Enum(
            IngestionStatus,
            name="ingestion_status",
            schema="metadata",
            values_callable=lambda enum: [member.value for member in enum],
            create_type=False,
        ),
        nullable=False,
    )
    details: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
