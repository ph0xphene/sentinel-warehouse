import enum
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from sentinel.models.base import Base


class EventType(enum.StrEnum):
    CREATE = "CREATE"
    MINT = "MINT"
    BURN = "BURN"
    TRANSFER = "TRANSFER"
    DEPOSIT = "DEPOSIT"
    WITHDRAWAL = "WITHDRAWAL"
    FEE = "FEE"
    INTEREST = "INTEREST"
    ADJUSTMENT = "ADJUSTMENT"


class Account(Base):
    __tablename__ = "accounts"
    __table_args__ = (
        UniqueConstraint("source_name", "external_id", name="uq_accounts_source_external_id"),
        {"schema": "core"},
    )

    account_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    source_name: Mapped[str] = mapped_column(String(255), nullable=False)
    external_id: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    account_type: Mapped[str] = mapped_column(String(50), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Asset(Base):
    __tablename__ = "assets"
    __table_args__ = (
        UniqueConstraint("source_name", "external_id", name="uq_assets_source_external_id"),
        CheckConstraint("decimals >= 0", name="ck_assets_non_negative_decimals"),
        {"schema": "core"},
    )

    asset_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    source_name: Mapped[str] = mapped_column(String(255), nullable=False)
    external_id: Mapped[str] = mapped_column(String(255), nullable=False)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    asset_type: Mapped[str] = mapped_column(String(50), nullable=False)
    decimals: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class FinancialTransaction(Base):
    __tablename__ = "transactions"
    __table_args__ = (
        UniqueConstraint("source_name", "external_id", name="uq_transactions_source_external_id"),
        CheckConstraint("amount >= 0", name="ck_transactions_non_negative_amount"),
        CheckConstraint(
            "from_account_id <> to_account_id", name="ck_transactions_distinct_accounts"
        ),
        {"schema": "core"},
    )

    transaction_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    batch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("metadata.ingestion_batches.batch_id"), nullable=False
    )
    source_name: Mapped[str] = mapped_column(String(255), nullable=False)
    external_id: Mapped[str] = mapped_column(String(255), nullable=False)
    from_account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("core.accounts.account_id"), nullable=False
    )
    to_account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("core.accounts.account_id"), nullable=False
    )
    asset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("core.assets.asset_id"), nullable=False
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(38, 18), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    description: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Balance(Base):
    __tablename__ = "balances"
    __table_args__ = (
        UniqueConstraint("source_name", "external_id", name="uq_balances_source_external_id"),
        UniqueConstraint(
            "account_id",
            "asset_id",
            "as_of",
            name="uq_balances_account_asset_as_of",
        ),
        {"schema": "core"},
    )

    balance_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    batch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("metadata.ingestion_batches.batch_id"), nullable=False
    )
    source_name: Mapped[str] = mapped_column(String(255), nullable=False)
    external_id: Mapped[str] = mapped_column(String(255), nullable=False)
    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("core.accounts.account_id"), nullable=False
    )
    asset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("core.assets.asset_id"), nullable=False
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(38, 18), nullable=False)
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class FinancialEvent(Base):
    """Canonical asset movement used for deterministic state reconstruction."""

    __tablename__ = "financial_events"
    __table_args__ = (
        Index(
            "uq_financial_events_source_external_id",
            "source_system",
            "external_id",
            unique=True,
            postgresql_where=text("canonical"),
        ),
        {"schema": "core"},
    )

    event_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    batch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("metadata.ingestion_batches.batch_id"),
        nullable=False,
        index=True,
    )
    source_system: Mapped[str] = mapped_column(String(255), nullable=False)
    external_id: Mapped[str] = mapped_column(String(255), nullable=False)
    chain_id: Mapped[int | None] = mapped_column(BigInteger)
    block_number: Mapped[int | None] = mapped_column(BigInteger)
    block_hash: Mapped[str | None] = mapped_column(String(66))
    canonical: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    asset_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("core.assets.asset_id")
    )
    account_from_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("core.accounts.account_id")
    )
    account_to_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("core.accounts.account_id")
    )
    amount: Mapped[Decimal | None] = mapped_column(Numeric(38, 18))
    event_metadata: Mapped[dict[str, object]] = mapped_column(
        "metadata", JSONB, nullable=False, default=dict
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
