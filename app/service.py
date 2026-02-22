import json
import uuid
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from typing import Optional, Tuple
from uuid import UUID

from sqlalchemy import select
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

SYSTEM_TREASURY = "system_treasury"
SYSTEM_BONUS_POOL = "system_bonus_pool"
SYSTEM_REVENUE = "system_revenue"

_IS_SQLITE = settings.DATABASE_URL.startswith("sqlite")


def _get_active_asset_type(db: Session, asset_type_id: UUID) -> AssetType:
    asset = db.get(AssetType, asset_type_id)
    if not asset or not asset.is_active:
        raise AssetTypeNotFoundError(str(asset_type_id))
    return asset


def _get_active_account(db: Session, account_id: UUID) -> Account:
    account = db.get(Account, account_id)
    if not account or not account.is_active:
        raise AccountNotFoundError(str(account_id))
    return account


def _get_system_account(db: Session, username: str) -> Account:
    account = db.execute(
        select(Account).where(Account.username == username, Account.is_system == True)
    ).scalar_one_or_none()
    if not account:
        raise AccountNotFoundError(f"system:{username}")
    return account


def _lock_wallet(db: Session, account_id: UUID, asset_type_id: UUID) -> Wallet:
    q = select(Wallet).where(
        Wallet.account_id == account_id,
        Wallet.asset_type_id == asset_type_id,
    )
    if not _IS_SQLITE:
        q = q.with_for_update()

    wallet = db.execute(q).scalar_one_or_none()

    if wallet is None:
        raise WalletNotFoundError(str(account_id), str(asset_type_id))
    return wallet


def _ensure_wallet(db: Session, account_id: UUID, asset_type_id: UUID) -> Wallet:
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
        db.flush()
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
        amount=-amount,
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
    new_balance = wallet.balance + amount

    wallet.balance = new_balance
    wallet.version += 1
    wallet.updated_at = datetime.now(timezone.utc)

    tx = Transaction(
        reference_id=ref_id,
        transaction_type=tx_type,
        wallet_id=wallet.id,
        amount=amount,
        balance_after=new_balance,
        description=description,
        idempotency_key=idempotency_key,
        metadata_=json.dumps(metadata) if metadata else None,
    )
    db.add(tx)
    return tx


def _check_idempotency(db: Session, key: str, endpoint: str) -> Optional[dict]:
    record = db.execute(
        select(IdempotencyKey).where(IdempotencyKey.key == key)
    ).scalar_one_or_none()

    if record is None:
        return None

    if record.endpoint != endpoint:
        raise IdempotencyConflictError(key)

    return json.loads(record.response_body)


def _store_idempotency(
    db: Session,
    key: str,
    endpoint: str,
    response: dict,
    ttl_hours: int = None,
) -> None:
    ttl = ttl_hours or settings.IDEMPOTENCY_KEY_TTL_HOURS
    record = IdempotencyKey(
        key=key,
        endpoint=endpoint,
        response_body=json.dumps(response, default=str),
        expires_at=datetime.now(timezone.utc) + timedelta(hours=ttl),
    )
    db.add(record)


def top_up(
    db: Session,
    user_account_id: UUID,
    asset_type_id: UUID,
    amount: Decimal,
    payment_reference: Optional[str] = None,
    description: Optional[str] = None,
    idempotency_key: Optional[str] = None,
) -> dict:
    ENDPOINT = "top_up"

    if idempotency_key:
        cached = _check_idempotency(db, idempotency_key, ENDPOINT)
        if cached is not None:
            raise DuplicateIdempotentRequestError(idempotency_key, cached)

    _get_active_account(db, user_account_id)
    asset = _get_active_asset_type(db, asset_type_id)
    treasury = _get_system_account(db, SYSTEM_TREASURY)

    src_wallet = _lock_wallet(db, treasury.id, asset_type_id)
    dst_wallet = _lock_wallet(db, user_account_id, asset_type_id)

    if src_wallet.balance < amount:
        raise InsufficientFundsError(
            float(src_wallet.balance), float(amount), asset.symbol
        )

    ref_id = uuid.uuid4()
    metadata = {"payment_reference": payment_reference} if payment_reference else None

    _apply_debit(db, src_wallet, amount, ref_id, "TOPUP",
                 f"Treasury debit for top-up: {description or ''}",
                 idempotency_key, metadata)
    credit_tx = _apply_credit(db, dst_wallet, amount, ref_id, "TOPUP",
                               description or f"Top-up of {amount} {asset.symbol}",
                               idempotency_key, metadata)

    db.flush()

    result = {
        "reference_id": str(ref_id),
        "transaction_type": "TOPUP",
        "amount": str(amount),
        "balance_after": str(credit_tx.balance_after),
        "message": f"Successfully credited {amount} {asset.symbol} to your wallet.",
    }

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
    ENDPOINT = "spend"

    if idempotency_key:
        cached = _check_idempotency(db, idempotency_key, ENDPOINT)
        if cached is not None:
            raise DuplicateIdempotentRequestError(idempotency_key, cached)

    _get_active_account(db, user_account_id)
    asset = _get_active_asset_type(db, asset_type_id)
    revenue = _get_system_account(db, SYSTEM_REVENUE)

    src_wallet = _lock_wallet(db, user_account_id, asset_type_id)

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


def get_balance(
    db: Session,
    account_id: UUID,
    asset_type_id: UUID,
) -> Tuple[Wallet, Account, AssetType]:
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
    return db.execute(
        select(AssetType).where(AssetType.is_active == True)
    ).scalars().all()


def list_accounts(db: Session, include_system: bool = False) -> list:
    q = select(Account).where(Account.is_active == True)
    if not include_system:
        q = q.where(Account.is_system == False)
    return db.execute(q).scalars().all()
