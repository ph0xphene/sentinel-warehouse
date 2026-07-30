import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from sentinel.models.base import Base


class RawFinancialRecord(Base):
    """Immutable source record retained exactly as received."""

    __tablename__ = "financial_records"
    __table_args__ = {"schema": "raw"}

    record_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    batch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("metadata.ingestion_batches.batch_id"),
        nullable=False,
        index=True,
    )
    source_name: Mapped[str] = mapped_column(String(255), nullable=False)
    record_type: Mapped[str] = mapped_column(String(50), nullable=False)
    external_id: Mapped[str | None] = mapped_column(String(255))
    payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    chain_id: Mapped[int | None] = mapped_column(BigInteger)
    block_number: Mapped[int | None] = mapped_column(BigInteger)
    block_hash: Mapped[str | None] = mapped_column(String(66))
    canonical: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class RawEthereumTransaction(Base):
    """Source-native Ethereum transaction envelope."""

    __tablename__ = "ethereum_transactions"
    __table_args__ = {"schema": "raw"}

    batch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("metadata.ingestion_batches.batch_id"),
        primary_key=True,
    )
    tx_hash: Mapped[str] = mapped_column(String(66), primary_key=True)
    chain_id: Mapped[int | None] = mapped_column(BigInteger)
    block_number: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    block_hash: Mapped[str | None] = mapped_column(String(66))
    block_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    from_address: Mapped[str] = mapped_column(String(42), nullable=False)
    to_address: Mapped[str | None] = mapped_column(String(42))
    success: Mapped[bool] = mapped_column(Boolean, nullable=False)
    canonical: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    transaction_metadata: Mapped[dict[str, object]] = mapped_column(
        "metadata", JSONB, nullable=False, default=dict
    )


class RawEthereumTransfer(Base):
    """Source-native ERC-20 Transfer log."""

    __tablename__ = "ethereum_transfers"
    __table_args__ = {"schema": "raw"}

    batch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("metadata.ingestion_batches.batch_id"),
        primary_key=True,
    )
    tx_hash: Mapped[str] = mapped_column(String(66), primary_key=True)
    log_index: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    chain_id: Mapped[int | None] = mapped_column(BigInteger)
    block_number: Mapped[int | None] = mapped_column(BigInteger)
    block_hash: Mapped[str | None] = mapped_column(String(66))
    token_address: Mapped[str] = mapped_column(String(42), nullable=False)
    from_address: Mapped[str] = mapped_column(String(42), nullable=False)
    to_address: Mapped[str] = mapped_column(String(42), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(78, 0), nullable=False)
    canonical: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class RawEthereumBlock(Base):
    """One auditable observation of an Ethereum block header."""

    __tablename__ = "ethereum_blocks"
    __table_args__ = (
        UniqueConstraint(
            "batch_id",
            "chain_id",
            "block_number",
            "block_hash",
            name="uq_ethereum_blocks_batch_identity",
        ),
        {"schema": "raw"},
    )

    observation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    batch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("metadata.ingestion_batches.batch_id"),
        nullable=False,
        index=True,
    )
    source_name: Mapped[str] = mapped_column(String(255), nullable=False)
    chain_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    block_number: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    block_hash: Mapped[str] = mapped_column(String(66), nullable=False)
    parent_hash: Mapped[str] = mapped_column(String(66), nullable=False)
    block_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    canonical: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class RawEthereumLog(Base):
    """Immutable JSON-RPC log observation, including unknown event signatures."""

    __tablename__ = "ethereum_logs"
    __table_args__ = (
        UniqueConstraint(
            "batch_id",
            "tx_hash",
            "log_index",
            name="uq_ethereum_logs_batch_position",
        ),
        {"schema": "raw"},
    )

    log_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    batch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("metadata.ingestion_batches.batch_id"),
        nullable=False,
        index=True,
    )
    source_name: Mapped[str] = mapped_column(String(255), nullable=False)
    chain_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    block_number: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    block_hash: Mapped[str] = mapped_column(String(66), nullable=False)
    tx_hash: Mapped[str] = mapped_column(String(66), nullable=False)
    log_index: Mapped[int] = mapped_column(BigInteger, nullable=False)
    contract_address: Mapped[str] = mapped_column(String(42), nullable=False)
    topics: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    data: Mapped[str] = mapped_column(Text, nullable=False)
    removed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    canonical: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
