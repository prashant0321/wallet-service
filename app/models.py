"""
SQLAlchemy ORM models for the Wallet Service.
"""
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
    """
    Defines the virtual currency type (e.g. Gold Coins, Diamonds, Loyalty Points).
    A platform can have multiple asset types.
    """
    __tablename__ = "asset_types"

    id = Column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(100), nullable=False, unique=True)
    symbol = Column(String(20), nullable=False, unique=True)   # e.g. "GC", "DIA", "LP"
    description = Column(Text, nullable=True)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)

    wallets = relationship("Wallet", back_populates="asset_type")

    def __repr__(self):
        return f"<AssetType {self.name} ({self.symbol})>"


class Account(Base):
    """
    Represents either a real user or a system account (Treasury, Revenue, etc.).
    System accounts act as the source/sink of funds for top-ups and bonuses.
    """
    __tablename__ = "accounts"

    id = Column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    username = Column(String(150), nullable=False, unique=True)
    email = Column(String(255), nullable=True, unique=True)
    is_system = Column(Boolean, nullable=False, default=False)   # True for treasury/system
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)

    wallets = relationship("Wallet", back_populates="account")

    def __repr__(self):
        return f"<Account {self.username} system={self.is_system}>"


class Wallet(Base):
    """
    One wallet per (account, asset_type) pair.
    Stores the current balance as a denormalized, always-up-to-date snapshot.
    The source of truth for the balance is the ledger (transactions table),
    but we keep this for O(1) balance reads.
    """
    __tablename__ = "wallets"

    id = Column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    account_id = Column(Uuid(as_uuid=True), ForeignKey("accounts.id"), nullable=False)
    asset_type_id = Column(Uuid(as_uuid=True), ForeignKey("asset_types.id"), nullable=False)
    balance = Column(Numeric(precision=20, scale=4), nullable=False, default=Decimal("0"))
    version = Column(Integer, nullable=False, default=0)  # optimistic lock counter
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
    TOPUP = "TOPUP"          # User buys credits (real money → virtual credits)
    BONUS = "BONUS"          # System gives free credits
    SPEND = "SPEND"          # User spends credits inside the app
    REFUND = "REFUND"        # Credits returned to user
    ADJUSTMENT = "ADJUSTMENT"  # Admin correction


class Transaction(Base):
    """
    Immutable double-entry ledger record.
    Every credit/debit is captured as a signed amount on a wallet.
    For each business event, exactly two (or more) entries are created:
      - debit entry on the source wallet  (negative amount)
      - credit entry on the destination wallet (positive amount)
    This keeps the ledger balanced at all times.
    """
    __tablename__ = "transactions"

    id = Column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # Group debit + credit entries for the same business event
    reference_id = Column(Uuid(as_uuid=True), nullable=False, index=True, default=uuid.uuid4)
    transaction_type = Column(String(20), nullable=False)
    wallet_id = Column(Uuid(as_uuid=True), ForeignKey("wallets.id"), nullable=False)
    amount = Column(Numeric(precision=20, scale=4), nullable=False)   # positive=credit, negative=debit
    balance_after = Column(Numeric(precision=20, scale=4), nullable=False)
    description = Column(Text, nullable=True)
    idempotency_key = Column(String(255), nullable=True, index=True)
    metadata_ = Column("metadata", Text, nullable=True)  # JSON string for extra data
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)

    wallet = relationship("Wallet")

    __table_args__ = (
        Index("ix_transaction_wallet_created", "wallet_id", "created_at"),
        Index("ix_transaction_type", "transaction_type"),
    )

    def __repr__(self):
        return f"<Transaction {self.transaction_type} amount={self.amount} ref={self.reference_id}>"


class IdempotencyKey(Base):
    """
    Stores the result of already-processed requests so that retried
    requests return the same response without re-executing the transaction.
    """
    __tablename__ = "idempotency_keys"

    id = Column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    key = Column(String(255), nullable=False, unique=True)
    endpoint = Column(String(100), nullable=False)
    response_body = Column(Text, nullable=False)  # JSON-serialized response
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    expires_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_idempotency_key", "key"),
    )

    def __repr__(self):
        return f"<IdempotencyKey {self.key}>"
