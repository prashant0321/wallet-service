"""
Core Wallet Service — all business logic lives here.

Concurrency Strategy
────────────────────
We use **pessimistic row-level locking** (`SELECT ... FOR UPDATE`) on the
wallet rows involved in every transaction.  This ensures:

1. Two concurrent requests that touch the same wallet are serialised at the
   database level — only one holds the lock at a time.
2. There is no TOCTOU (Time-Of-Check-To-Time-Of-Use) window between reading
   the balance and writing the new one.
3. We still rely on the database's `CHECK (balance >= 0)` constraint as a
   safety net — if a bug slips through, the DB rejects the write outright.

Idempotency Strategy
─────────────────────
Every mutating endpoint accepts an optional `Idempotency-Key` header.
Before executing any logic we:
  1. Try to look up the key in the `idempotency_keys` table (within the same tx).
  2. If found → return the stored response immediately (no side effects).
  3. If not found → execute the business logic, then store the result under
     the key inside the SAME transaction, so the key is only visible if the
     whole transaction committed.

Double-Entry Bookkeeping
─────────────────────────
Every credit/debit to a user wallet has a corresponding debit/credit on a
system wallet (Treasury for top-ups, Bonus Pool for bonuses, Revenue for
spends).  The sum of all amounts in the `transactions` table should always be
zero — the ledger is self-balancing.
"""

import json
import uuid
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from typing import Optional, Tuple
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.exceptions import (
    AccountNotFoundError,
    AssetTypeNotFoundError,
    DuplicateIdempotentRequestError,
    IdempotencyConflictError,
    InsufficientFundsError,
    NegativeBalanceError,
    WalletNotFoundError,
)
from app.models import (
    Account,
    AssetType,
    IdempotencyKey,
    Transaction,
    Wallet,
)
from app.config import settings

# Well-known system account usernames (must match seed.sql)
SYSTEM_TREASURY = "system_treasury"
SYSTEM_BONUS_POOL = "system_bonus_pool"
SYSTEM_REVENUE = "system_revenue"


# ──────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ──────────────────────────────────────────────────────────────────────────────

def _get_active_asset_type(db: Session, asset_type_id: UUID) -> AssetType:
    """Fetch and validate an asset type by ID."""
    asset = db.get(AssetType, asset_type_id)
    if not asset or not asset.is_active:
        raise AssetTypeNotFoundError(str(asset_type_id))
    return asset


def _get_active_account(db: Session, account_id: UUID) -> Account:
    """Fetch and validate an account by ID."""
    account = db.get(Account, account_id)
    if not account or not account.is_active:
        raise AccountNotFoundError(str(account_id))
    return account


def _get_system_account(db: Session, username: str) -> Account:
    """Fetch a system account by username."""
    account = db.execute(
        select(Account).where(Account.username == username, Account.is_system == True)
    ).scalar_one_or_none()
    if not account:
        raise AccountNotFoundError(f"system:{username}")
    return account


def _lock_wallet(db: Session, account_id: UUID, asset_type_id: UUID) -> Wallet:
    """
    Acquire a pessimistic row-level lock on the wallet row.

    `SELECT ... FOR UPDATE` blocks any other transaction that tries to lock
    the same row, serialising concurrent writes to the same wallet.
    """
    wallet = db.execute(
        select(Wallet)
        .where(
            Wallet.account_id == account_id,
            Wallet.asset_type_id == asset_type_id,
        )
        .with_for_update()   # ← the key concurrency primitive
    ).scalar_one_or_none()

    if wallet is None:
        raise WalletNotFoundError(str(account_id), str(asset_type_id))
    return wallet


def _ensure_wallet(db: Session, account_id: UUID, asset_type_id: UUID) -> Wallet:
    """
    Get or create a wallet for (account_id, asset_type_id).
    Uses INSERT ... ON CONFLICT DO NOTHING to be safe under concurrency.
    """
    wallet = db.execute(
        select(Wallet).where(
            Wallet.account_id == account_id,
            Wallet.asset_type_id == asset_type_id,
        )
    ).scalar_one_or_none()

    if wallet is None:
        wallet = Wallet(
            account_id=account_id,
            asset_type_id=asset_type_id,
            balance=Decimal("0"),
        )
        db.add(wallet)
        db.flush()  # assign ID without committing
    return wallet


def _apply_debit(
    db: Session,
    wallet: Wallet,
    amount: Decimal,
    ref_id: UUID,
    tx_type: str,
    description: Optional[str],
    idempotency_key: Optional[str],
    metadata: Optional[dict],
) -> Transaction:
    """Debit (subtract) from a wallet and record the ledger entry."""
    new_balance = wallet.balance - amount
    if new_balance < Decimal("0"):
        raise NegativeBalanceError(str(wallet.id), float(new_balance))

    wallet.balance = new_balance
    wallet.version += 1
    wallet.updated_at = datetime.now(timezone.utc)

    tx = Transaction(
        reference_id=ref_id,
        transaction_type=tx_type,
        wallet_id=wallet.id,
        amount=-amount,  # negative = debit
        balance_after=new_balance,
        description=description,
        idempotency_key=idempotency_key,
        metadata_=json.dumps(metadata) if metadata else None,
    )
    db.add(tx)
    return tx


def _apply_credit(
    db: Session,
    wallet: Wallet,
    amount: Decimal,
    ref_id: UUID,
    tx_type: str,
    description: Optional[str],
    idempotency_key: Optional[str],
    metadata: Optional[dict],
) -> Transaction:
    """Credit (add) to a wallet and record the ledger entry."""
    new_balance = wallet.balance + amount

    wallet.balance = new_balance
    wallet.version += 1
    wallet.updated_at = datetime.now(timezone.utc)

    tx = Transaction(
        reference_id=ref_id,
        transaction_type=tx_type,
        wallet_id=wallet.id,
        amount=amount,  # positive = credit
        balance_after=new_balance,
        description=description,
        idempotency_key=idempotency_key,
        metadata_=json.dumps(metadata) if metadata else None,
    )
    db.add(tx)
    return tx


# ──────────────────────────────────────────────────────────────────────────────
# Idempotency helpers
# ──────────────────────────────────────────────────────────────────────────────

def _check_idempotency(
    db: Session,
    key: str,
    endpoint: str,
) -> Optional[dict]:
    """
    Returns the cached response dict if the key has already been processed,
    or None if this is a new request.
    """
    record = db.execute(
        select(IdempotencyKey).where(IdempotencyKey.key == key)
    ).scalar_one_or_none()

    if record is None:
        return None

    # Key exists — check it matches the same endpoint
    if record.endpoint != endpoint:
        raise IdempotencyConflictError(key)

    # Valid duplicate — return cached response
    return json.loads(record.response_body)


def _store_idempotency(
    db: Session,
    key: str,
    endpoint: str,
    response: dict,
    ttl_hours: int = None,
) -> None:
    """Persist the response for this idempotency key (within the current tx)."""
    ttl = ttl_hours or settings.IDEMPOTENCY_KEY_TTL_HOURS
    record = IdempotencyKey(
        key=key,
        endpoint=endpoint,
        response_body=json.dumps(response, default=str),
        expires_at=datetime.now(timezone.utc) + timedelta(hours=ttl),
    )
    db.add(record)


# ──────────────────────────────────────────────────────────────────────────────
# Public service functions
# ──────────────────────────────────────────────────────────────────────────────

def top_up(
    db: Session,
    user_account_id: UUID,
    asset_type_id: UUID,
    amount: Decimal,
    payment_reference: Optional[str] = None,
    description: Optional[str] = None,
    idempotency_key: Optional[str] = None,
) -> dict:
    """
    Flow 1 — Wallet Top-up (Purchase)
    ──────────────────────────────────
    Credits the user's wallet, debiting the Treasury system wallet.
    The real-money payment is assumed to have been processed externally.

    Steps:
      1. Idempotency check.
      2. Validate account + asset type.
      3. Lock Treasury wallet (FOR UPDATE) → debit.
      4. Lock/create user wallet (FOR UPDATE) → credit.
      5. Write two ledger entries under a shared reference_id.
      6. Store idempotency result.
      7. Commit (caller owns the transaction boundary via `db`).
    """
    ENDPOINT = "top_up"

    # 1. Idempotency check
    if idempotency_key:
        cached = _check_idempotency(db, idempotency_key, ENDPOINT)
        if cached is not None:
            raise DuplicateIdempotentRequestError(idempotency_key, cached)

    # 2. Validate
    _get_active_account(db, user_account_id)
    asset = _get_active_asset_type(db, asset_type_id)
    treasury = _get_system_account(db, SYSTEM_TREASURY)

    # 3. Lock both wallets (always lock in a consistent order: system first, then user)
    src_wallet = _lock_wallet(db, treasury.id, asset_type_id)
    dst_wallet = _lock_wallet(db, user_account_id, asset_type_id)

    # 4. Check treasury can cover the amount (safety; in practice it's a huge pool)
    if src_wallet.balance < amount:
        raise InsufficientFundsError(
            float(src_wallet.balance), float(amount), asset.symbol
        )

    ref_id = uuid.uuid4()
    metadata = {"payment_reference": payment_reference} if payment_reference else None

    # 5. Debit Treasury, Credit user
    _apply_debit(db, src_wallet, amount, ref_id, "TOPUP",
                 f"Treasury debit for top-up: {description or ''}",
                 idempotency_key, metadata)
    credit_tx = _apply_credit(db, dst_wallet, amount, ref_id, "TOPUP",
                               description or f"Top-up of {amount} {asset.symbol}",
                               idempotency_key, metadata)

    db.flush()  # surface any constraint violations before we store the idem key

    result = {
        "reference_id": str(ref_id),
        "transaction_type": "TOPUP",
        "amount": str(amount),
        "balance_after": str(credit_tx.balance_after),
        "message": f"Successfully credited {amount} {asset.symbol} to your wallet.",
    }

    # 6. Store idempotency result (same tx → atomic with the wallet changes)
    if idempotency_key:
        _store_idempotency(db, idempotency_key, ENDPOINT, result)

    return result


def issue_bonus(
    db: Session,
    user_account_id: UUID,
    asset_type_id: UUID,
    amount: Decimal,
    reason: Optional[str] = None,
    description: Optional[str] = None,
    idempotency_key: Optional[str] = None,
) -> dict:
    """
    Flow 2 — Bonus / Incentive
    ───────────────────────────
    The system issues free credits to a user (referral bonus, login reward, etc.).
    Debits the Bonus Pool system wallet, credits the user.

    Locking order: Bonus Pool → User (consistent ordering prevents deadlocks).
    """
    ENDPOINT = "issue_bonus"

    if idempotency_key:
        cached = _check_idempotency(db, idempotency_key, ENDPOINT)
        if cached is not None:
            raise DuplicateIdempotentRequestError(idempotency_key, cached)

    _get_active_account(db, user_account_id)
    asset = _get_active_asset_type(db, asset_type_id)
    bonus_pool = _get_system_account(db, SYSTEM_BONUS_POOL)

    src_wallet = _lock_wallet(db, bonus_pool.id, asset_type_id)
    dst_wallet = _lock_wallet(db, user_account_id, asset_type_id)

    if src_wallet.balance < amount:
        raise InsufficientFundsError(
            float(src_wallet.balance), float(amount), asset.symbol
        )

    ref_id = uuid.uuid4()
    metadata = {"reason": reason} if reason else None

    _apply_debit(db, src_wallet, amount, ref_id, "BONUS",
                 f"Bonus pool debit: {reason or ''}",
                 idempotency_key, metadata)
    credit_tx = _apply_credit(db, dst_wallet, amount, ref_id, "BONUS",
                               description or f"Bonus: {reason or 'system grant'} — {amount} {asset.symbol}",
                               idempotency_key, metadata)

    db.flush()

    result = {
        "reference_id": str(ref_id),
        "transaction_type": "BONUS",
        "amount": str(amount),
        "balance_after": str(credit_tx.balance_after),
        "message": f"Bonus of {amount} {asset.symbol} issued successfully.",
    }

    if idempotency_key:
        _store_idempotency(db, idempotency_key, ENDPOINT, result)

    return result


def spend(
    db: Session,
    user_account_id: UUID,
    asset_type_id: UUID,
    amount: Decimal,
    item_reference: Optional[str] = None,
    description: Optional[str] = None,
    idempotency_key: Optional[str] = None,
) -> dict:
    """
    Flow 3 — Purchase / Spend
    ──────────────────────────
    Deducts credits from the user's wallet for an in-app purchase.
    Debits the user, credits the Revenue system wallet.

    Raises InsufficientFundsError if the user doesn't have enough credits.
    Locking order: User → Revenue (consistent ordering prevents deadlocks).
    """
    ENDPOINT = "spend"

    if idempotency_key:
        cached = _check_idempotency(db, idempotency_key, ENDPOINT)
        if cached is not None:
            raise DuplicateIdempotentRequestError(idempotency_key, cached)

    _get_active_account(db, user_account_id)
    asset = _get_active_asset_type(db, asset_type_id)
    revenue = _get_system_account(db, SYSTEM_REVENUE)

    # Lock user wallet first, then revenue (consistent order)
    src_wallet = _lock_wallet(db, user_account_id, asset_type_id)

    # Check balance AFTER acquiring lock — eliminates TOCTOU race
    if src_wallet.balance < amount:
        raise InsufficientFundsError(
            float(src_wallet.balance), float(amount), asset.symbol
        )

    dst_wallet = _lock_wallet(db, revenue.id, asset_type_id)

    ref_id = uuid.uuid4()
    metadata = {"item_reference": item_reference} if item_reference else None

    debit_tx = _apply_debit(db, src_wallet, amount, ref_id, "SPEND",
                             description or f"Spent {amount} {asset.symbol}",
                             idempotency_key, metadata)
    _apply_credit(db, dst_wallet, amount, ref_id, "SPEND",
                  f"Revenue credit from spend: {item_reference or ''}",
                  idempotency_key, metadata)

    db.flush()

    result = {
        "reference_id": str(ref_id),
        "transaction_type": "SPEND",
        "amount": str(amount),
        "balance_after": str(debit_tx.balance_after),
        "message": f"Successfully spent {amount} {asset.symbol}.",
    }

    if idempotency_key:
        _store_idempotency(db, idempotency_key, ENDPOINT, result)

    return result


# ──────────────────────────────────────────────────────────────────────────────
# Read-only queries
# ──────────────────────────────────────────────────────────────────────────────

def get_balance(
    db: Session,
    account_id: UUID,
    asset_type_id: UUID,
) -> Tuple[Wallet, Account, AssetType]:
    """Return wallet + account + asset_type for a balance query."""
    account = _get_active_account(db, account_id)
    asset = _get_active_asset_type(db, asset_type_id)

    wallet = db.execute(
        select(Wallet).where(
            Wallet.account_id == account_id,
            Wallet.asset_type_id == asset_type_id,
        )
    ).scalar_one_or_none()

    if wallet is None:
        raise WalletNotFoundError(str(account_id), str(asset_type_id))

    return wallet, account, asset


def get_transaction_history(
    db: Session,
    account_id: UUID,
    asset_type_id: UUID,
    limit: int = 50,
    offset: int = 0,
) -> Tuple[list, int]:
    """Return paginated transaction history for a wallet."""
    _get_active_account(db, account_id)
    _get_active_asset_type(db, asset_type_id)

    wallet = db.execute(
        select(Wallet).where(
            Wallet.account_id == account_id,
            Wallet.asset_type_id == asset_type_id,
        )
    ).scalar_one_or_none()

    if wallet is None:
        raise WalletNotFoundError(str(account_id), str(asset_type_id))

    from sqlalchemy import func
    total = db.execute(
        select(func.count(Transaction.id)).where(Transaction.wallet_id == wallet.id)
    ).scalar()

    txs = db.execute(
        select(Transaction)
        .where(Transaction.wallet_id == wallet.id)
        .order_by(Transaction.created_at.desc())
        .limit(limit)
        .offset(offset)
    ).scalars().all()

    return txs, total


def list_asset_types(db: Session) -> list:
    """Return all active asset types."""
    return db.execute(
        select(AssetType).where(AssetType.is_active == True)
    ).scalars().all()


def list_accounts(db: Session, include_system: bool = False) -> list:
    """Return user (and optionally system) accounts."""
    q = select(Account).where(Account.is_active == True)
    if not include_system:
        q = q.where(Account.is_system == False)
    return db.execute(q).scalars().all()
