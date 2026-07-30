import enum
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from sentinel.models.base import Base


class IncidentStatus(enum.StrEnum):
    OPEN = "OPEN"
    INVESTIGATING = "INVESTIGATING"
    RESOLVED = "RESOLVED"
    IGNORED = "IGNORED"


class InvariantResult(Base):
    """Persisted execution result for one security invariant."""

    __tablename__ = "invariants"
    __table_args__ = {"schema": "security"}

    invariant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    batch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("metadata.ingestion_batches.batch_id"),
        nullable=False,
        index=True,
    )
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    protocol_name: Mapped[str | None] = mapped_column(String(100))
    severity: Mapped[str] = mapped_column(String(20), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    execution_result: Mapped[str] = mapped_column(String(20), nullable=False)
    affected_records: Mapped[list[dict[str, object]]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Incident(Base):
    """Auditable investigation record created from a failed invariant."""

    __tablename__ = "incidents"
    __table_args__ = (
        UniqueConstraint(
            "batch_id",
            "incident_type",
            name="uq_incidents_batch_type",
        ),
        {"schema": "security"},
    )

    incident_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    incident_type: Mapped[str] = mapped_column(String(100), nullable=False)
    protocol_name: Mapped[str | None] = mapped_column(String(100))
    severity: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[IncidentStatus] = mapped_column(
        Enum(
            IncidentStatus,
            name="incident_status",
            schema="security",
            values_callable=lambda enum: [member.value for member in enum],
        ),
        nullable=False,
    )
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    batch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("metadata.ingestion_batches.batch_id"),
        nullable=False,
        index=True,
    )
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class IncidentEvidence(Base):
    """Structured evidence attached to an investigation incident."""

    __tablename__ = "incident_evidence"
    __table_args__ = {"schema": "security"}

    evidence_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    incident_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("security.incidents.incident_id"),
        nullable=False,
        index=True,
    )
    affected_entity: Mapped[str] = mapped_column(String(255), nullable=False)
    evidence_type: Mapped[str] = mapped_column(String(100), nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class AttackPattern(Base):
    """Curated label describing a reusable security incident pattern."""

    __tablename__ = "attack_patterns"
    __table_args__ = (
        UniqueConstraint("name", name="uq_attack_patterns_name"),
        {"schema": "security"},
    )

    pattern_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    subcategory_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("security.attack_subcategories.subcategory_id"),
        index=True,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    category: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)


class AttackCategory(Base):
    """Top-level security incident taxonomy category."""

    __tablename__ = "attack_categories"
    __table_args__ = (
        UniqueConstraint("name", name="uq_attack_categories_name"),
        {"schema": "security"},
    )

    category_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)


class AttackSubcategory(Base):
    """Specific attack class nested under one taxonomy category."""

    __tablename__ = "attack_subcategories"
    __table_args__ = (
        UniqueConstraint(
            "category_id",
            "name",
            name="uq_attack_subcategories_category_name",
        ),
        {"schema": "security"},
    )

    subcategory_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    category_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("security.attack_categories.category_id"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)


class IncidentCase(Base):
    """A reproducible security research case and its expected replay outcome."""

    __tablename__ = "incident_cases"
    __table_args__ = (
        UniqueConstraint("name", name="uq_incident_cases_name"),
        {"schema": "security"},
    )

    case_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    attack_pattern_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("security.attack_patterns.pattern_id"),
        index=True,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    protocol: Mapped[str] = mapped_column(String(100), nullable=False)
    chain: Mapped[str | None] = mapped_column(String(100))
    category: Mapped[str] = mapped_column(String(100), nullable=False)
    severity: Mapped[str] = mapped_column(String(20), nullable=False)
    confidence_level: Mapped[str | None] = mapped_column(String(20))
    affected_contracts: Mapped[list[str] | None] = mapped_column(JSONB)
    attacker_addresses: Mapped[list[str] | None] = mapped_column(JSONB)
    reference_transactions: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    external_references: Mapped[list[str] | None] = mapped_column(JSONB)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    replay_definition: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class AttackFlow(Base):
    """One ordered, human-readable step in an incident reconstruction."""

    __tablename__ = "attack_flows"
    __table_args__ = (
        UniqueConstraint("case_id", "step_number", name="uq_attack_flows_case_step"),
        {"schema": "security"},
    )

    flow_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("security.incident_cases.case_id"),
        nullable=False,
        index=True,
    )
    step_number: Mapped[int] = mapped_column(Integer, nullable=False)
    event_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("core.financial_events.event_id"),
    )
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)


class IncidentFeature(Base):
    """One deterministic numeric or categorical feature for a research case."""

    __tablename__ = "incident_features"
    __table_args__ = (
        CheckConstraint(
            "(numeric_value IS NOT NULL) <> (categorical_value IS NOT NULL)",
            name="ck_incident_features_exactly_one_value",
        ),
        {"schema": "security"},
    )

    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("security.incident_cases.case_id"),
        primary_key=True,
    )
    feature_name: Mapped[str] = mapped_column(String(100), primary_key=True)
    numeric_value: Mapped[Decimal | None] = mapped_column(Numeric(38, 18))
    categorical_value: Mapped[str | None] = mapped_column(Text)
