from datetime import datetime, timezone
from decimal import Decimal
from sqlalchemy import (
    Column, String, Numeric, Integer, ForeignKey,
    DateTime, Text, Boolean, Index, CheckConstraint, UniqueConstraint
)
from sqlalchemy.types import Uuid
from sqlalchemy.orm import relationship, declarative_base
import uuid

Base = declarative_base()


def utcnow():
    return datetime.now(timezone.utc)


class AssetType(Base):
    __tablename__ = "asset_types"

    id = Column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(100), nullable=False, unique=True)
    symbol = Column(String(20), nullable=False, unique=True)
    description = Column(Text, nullable=True)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)

    wallets = relationship("Wallet", back_populates="asset_type")

    def __repr__(self):
        return f"<AssetType {self.name} ({self.symbol})>"


class Account(Base):
    __tablename__ = "accounts"

    id = Column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    username = Column(String(150), nullable=False, unique=True)
    email = Column(String(255), nullable=True, unique=True)
    hashed_password = Column(String(255), nullable=True)
    is_system = Column(Boolean, nullable=False, default=False)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)

    wallets = relationship("Wallet", back_populates="account")

    def __repr__(self):
        return f"<Account {self.username} system={self.is_system}>"


class Wallet(Base):
    __tablename__ = "wallets"

    id = Column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    account_id = Column(Uuid(as_uuid=True), ForeignKey("accounts.id"), nullable=False)
    asset_type_id = Column(Uuid(as_uuid=True), ForeignKey("asset_types.id"), nullable=False)
    balance = Column(Numeric(precision=20, scale=4), nullable=False, default=Decimal("0"))
    version = Column(Integer, nullable=False, default=0)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)

    account = relationship("Account", back_populates="wallets")
    asset_type = relationship("AssetType", back_populates="wallets")

    __table_args__ = (
        UniqueConstraint("account_id", "asset_type_id", name="uq_wallet_account_asset"),
        CheckConstraint("balance >= 0", name="ck_wallet_balance_non_negative"),
        Index("ix_wallet_account_asset", "account_id", "asset_type_id"),
    )

    def __repr__(self):
        return f"<Wallet account={self.account_id} asset={self.asset_type_id} balance={self.balance}>"


class TransactionType(str):
    TOPUP = "TOPUP"
    BONUS = "BONUS"
    SPEND = "SPEND"
    REFUND = "REFUND"
    ADJUSTMENT = "ADJUSTMENT"


class Transaction(Base):
    __tablename__ = "transactions"

    id = Column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    reference_id = Column(Uuid(as_uuid=True), nullable=False, index=True, default=uuid.uuid4)
    transaction_type = Column(String(20), nullable=False)
    wallet_id = Column(Uuid(as_uuid=True), ForeignKey("wallets.id"), nullable=False)
    amount = Column(Numeric(precision=20, scale=4), nullable=False)
    balance_after = Column(Numeric(precision=20, scale=4), nullable=False)
    description = Column(Text, nullable=True)
    idempotency_key = Column(String(255), nullable=True, index=True)
    metadata_ = Column("metadata", Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)

    wallet = relationship("Wallet")

    __table_args__ = (
        Index("ix_transaction_wallet_created", "wallet_id", "created_at"),
        Index("ix_transaction_type", "transaction_type"),
    )

    def __repr__(self):
        return f"<Transaction {self.transaction_type} amount={self.amount} ref={self.reference_id}>"


class IdempotencyKey(Base):
    __tablename__ = "idempotency_keys"

    id = Column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    key = Column(String(255), nullable=False, unique=True)
    endpoint = Column(String(100), nullable=False)
    response_body = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    expires_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_idempotency_key", "key"),
    )

    def __repr__(self):
        return f"<IdempotencyKey {self.key}>"
