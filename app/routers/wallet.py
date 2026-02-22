from decimal import Decimal
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.exceptions import (
    AccountNotFoundError,
    AssetTypeNotFoundError,
    DuplicateIdempotentRequestError,
    IdempotencyConflictError,
    InsufficientFundsError,
    NegativeBalanceError,
    WalletNotFoundError,
)
from app.schemas import (
    BalanceResponse,
    BonusRequest,
    SpendRequest,
    TopUpRequest,
    TransactionListResponse,
    TransactionOut,
    TransactionResponse,
    AssetTypeOut,
    AccountOut,
)
import app.service as svc

router = APIRouter(prefix="/wallet", tags=["Wallet"])


def _handle_service_errors(exc: Exception) -> HTTPException:
    mapping = {
        InsufficientFundsError: (status.HTTP_402_PAYMENT_REQUIRED, "INSUFFICIENT_FUNDS"),
        WalletNotFoundError:    (status.HTTP_404_NOT_FOUND,         "WALLET_NOT_FOUND"),
        AccountNotFoundError:   (status.HTTP_404_NOT_FOUND,         "ACCOUNT_NOT_FOUND"),
        AssetTypeNotFoundError: (status.HTTP_404_NOT_FOUND,         "ASSET_TYPE_NOT_FOUND"),
        IdempotencyConflictError: (status.HTTP_409_CONFLICT,        "IDEMPOTENCY_CONFLICT"),
        NegativeBalanceError:   (status.HTTP_500_INTERNAL_SERVER_ERROR, "NEGATIVE_BALANCE"),
    }
    for exc_cls, (http_code, code) in mapping.items():
        if isinstance(exc, exc_cls):
            return HTTPException(status_code=http_code, detail={"code": code, "message": str(exc)})
    return HTTPException(status_code=500, detail={"code": "INTERNAL_ERROR", "message": str(exc)})



@router.get(
    "/balance/{account_id}/{asset_type_id}",
    response_model=BalanceResponse,
    summary="Get wallet balance",
)
def get_balance(
    account_id: UUID,
    asset_type_id: UUID,
    db: Session = Depends(get_db),
):
    try:
        wallet, account, asset = svc.get_balance(db, account_id, asset_type_id)
    except Exception as e:
        raise _handle_service_errors(e)

    return BalanceResponse(
        account_id=account.id,
        username=account.username,
        asset_type=asset.name,
        symbol=asset.symbol,
        balance=wallet.balance,
    )


@router.get(
    "/transactions/{account_id}/{asset_type_id}",
    response_model=TransactionListResponse,
    summary="Get transaction history",
)
def get_transactions(
    account_id: UUID,
    asset_type_id: UUID,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    try:
        txs, total = svc.get_transaction_history(db, account_id, asset_type_id, limit, offset)
        _, _, asset = svc.get_balance(db, account_id, asset_type_id)
    except Exception as e:
        raise _handle_service_errors(e)

    return TransactionListResponse(
        account_id=account_id,
        asset_type=asset.name,
        transactions=[TransactionOut.model_validate(t) for t in txs],
        total=total,
    )


@router.get(
    "/asset-types",
    response_model=List[AssetTypeOut],
    summary="List all asset types",
)
def list_asset_types(db: Session = Depends(get_db)):
    return svc.list_asset_types(db)


@router.get(
    "/accounts",
    response_model=List[AccountOut],
    summary="List user accounts",
)
def list_accounts(
    include_system: bool = Query(default=False, description="Include system accounts"),
    db: Session = Depends(get_db),
):
    return svc.list_accounts(db, include_system=include_system)


@router.post(
    "/topup",
    response_model=TransactionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Top up a user wallet",
)
def top_up(
    request: TopUpRequest,
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    db: Session = Depends(get_db),
):
    try:
        result = svc.top_up(
            db=db,
            user_account_id=request.user_account_id,
            asset_type_id=request.asset_type_id,
            amount=request.amount,
            payment_reference=request.payment_reference,
            description=request.description,
            idempotency_key=idempotency_key,
        )
        db.commit()
    except DuplicateIdempotentRequestError as e:
        return TransactionResponse(**e.cached_response)
    except Exception as e:
        db.rollback()
        raise _handle_service_errors(e)

    return TransactionResponse(**result)


@router.post(
    "/bonus",
    response_model=TransactionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Issue a bonus to a user",
)
def issue_bonus(
    request: BonusRequest,
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    db: Session = Depends(get_db),
):
    try:
        result = svc.issue_bonus(
            db=db,
            user_account_id=request.user_account_id,
            asset_type_id=request.asset_type_id,
            amount=request.amount,
            reason=request.reason,
            description=request.description,
            idempotency_key=idempotency_key,
        )
        db.commit()
    except DuplicateIdempotentRequestError as e:
        return TransactionResponse(**e.cached_response)
    except Exception as e:
        db.rollback()
        raise _handle_service_errors(e)

    return TransactionResponse(**result)


@router.post(
    "/spend",
    response_model=TransactionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Spend credits for an in-app purchase",
)
def spend(
    request: SpendRequest,
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    db: Session = Depends(get_db),
):
    try:
        result = svc.spend(
            db=db,
            user_account_id=request.user_account_id,
            asset_type_id=request.asset_type_id,
            amount=request.amount,
            item_reference=request.item_reference,
            description=request.description,
            idempotency_key=idempotency_key,
        )
        db.commit()
    except DuplicateIdempotentRequestError as e:
        return TransactionResponse(**e.cached_response)
    except Exception as e:
        db.rollback()
        raise _handle_service_errors(e)

    return TransactionResponse(**result)
