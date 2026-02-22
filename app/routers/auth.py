from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from jose import jwt
import bcrypt
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import Account
from app.schemas import LoginRequest, RegisterRequest, TokenResponse

router = APIRouter(prefix="/auth", tags=["Auth"])


def _hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def _verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))


def _create_access_token(data: dict, expires_minutes: Optional[int] = None) -> str:
    expire = datetime.now(timezone.utc) + timedelta(
        minutes=expires_minutes or settings.ACCESS_TOKEN_EXPIRE_MINUTES
    )
    payload = {**data, "exp": expire}
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
def register(body: RegisterRequest, db: Session = Depends(get_db)):
    if db.query(Account).filter(Account.username == body.username).first():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "USERNAME_TAKEN", "message": "Username already exists"},
        )
    if body.email and db.query(Account).filter(Account.email == body.email).first():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "EMAIL_TAKEN", "message": "Email already registered"},
        )

    account = Account(
        username=body.username,
        email=body.email,
        hashed_password=_hash_password(body.password),
        is_system=False,
        is_active=True,
    )
    db.add(account)
    db.commit()
    db.refresh(account)

    token = _create_access_token({"sub": str(account.id), "username": account.username})
    return TokenResponse(access_token=token, account_id=account.id, username=account.username)


@router.post("/login", response_model=TokenResponse)
def login(body: LoginRequest, db: Session = Depends(get_db)):
    account = db.query(Account).filter(Account.username == body.username).first()

    if not account or not account.hashed_password:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "INVALID_CREDENTIALS", "message": "Invalid username or password"},
        )
    if not _verify_password(body.password, account.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "INVALID_CREDENTIALS", "message": "Invalid username or password"},
        )
    token = _create_access_token({"sub": str(account.id), "username": account.username})
    return TokenResponse(access_token=token, account_id=account.id, username=account.username)


@router.delete("/accounts/{account_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_account(account_id: UUID, db: Session = Depends(get_db)):
    account = db.query(Account).filter(Account.id == account_id, Account.is_system == False).first()
    if not account:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "ACCOUNT_NOT_FOUND", "message": "Account not found"},
        )
    db.delete(account)
    db.commit()
