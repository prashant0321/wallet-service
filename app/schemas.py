"""
Pydantic schemas for request/response validation.
"""
from datetime import datetime
from decimal import Decimal
from typing import List, Optional
from uuid import UUID
from pydantic import BaseModel, Field, field_validator



class AssetTypeOut(BaseModel):
    id: UUID
    name: str
    symbol: str
    description: Optional[str] = None
    is_active: bool

    model_config = {"from_attributes": True}


class AccountOut(BaseModel):
    id: UUID
    username: str
    email: Optional[str] = None
    is_system: bool
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class WalletOut(BaseModel):
    id: UUID
    account_id: UUID
    asset_type_id: UUID
    balance: Decimal
    updated_at: datetime

    model_config = {"from_attributes": True}


class BalanceResponse(BaseModel):
    account_id: UUID
    username: str
    asset_type: str
    symbol: str
    balance: Decimal


class TopUpRequest(BaseModel):
    """
    Credits the user's wallet.
    Represents a user purchasing virtual credits with real money.
    The payment gateway is assumed to already have processed the payment.
    """
    user_account_id: UUID = Field(..., description="The user receiving the credits")
    asset_type_id: UUID = Field(..., description="Which virtual currency to credit")
    amount: Decimal = Field(..., gt=0, description="Amount to credit (must be > 0)")
    payment_reference: Optional[str] = Field(
        None,
        description="External payment gateway reference (stored in metadata)"
    )
    description: Optional[str] = Field(None, description="Human-readable note")

    @field_validator("amount")
    @classmethod
    def amount_positive(cls, v: Decimal) -> Decimal:
        if v <= 0:
            raise ValueError("Amount must be positive")
        return v


class BonusRequest(BaseModel):
    """
    System-issued free credits (referral bonus, login reward, etc.).
    """
    user_account_id: UUID = Field(..., description="The user receiving the bonus")
    asset_type_id: UUID = Field(..., description="Which virtual currency to credit")
    amount: Decimal = Field(..., gt=0, description="Bonus amount (must be > 0)")
    reason: Optional[str] = Field(None, description="Reason for the bonus")
    description: Optional[str] = Field(None, description="Human-readable note")

    @field_validator("amount")
    @classmethod
    def amount_positive(cls, v: Decimal) -> Decimal:
        if v <= 0:
            raise ValueError("Amount must be positive")
        return v


class SpendRequest(BaseModel):
    """
    Deducts credits from the user's wallet for an in-app purchase.
    """
    user_account_id: UUID = Field(..., description="The user spending the credits")
    asset_type_id: UUID = Field(..., description="Which virtual currency to deduct")
    amount: Decimal = Field(..., gt=0, description="Amount to deduct (must be > 0)")
    item_reference: Optional[str] = Field(
        None,
        description="Internal reference for the item/service being purchased"
    )
    description: Optional[str] = Field(None, description="Human-readable note")

    @field_validator("amount")
    @classmethod
    def amount_positive(cls, v: Decimal) -> Decimal:
        if v <= 0:
            raise ValueError("Amount must be positive")
        return v



class TransactionOut(BaseModel):
    id: UUID
    reference_id: UUID
    transaction_type: str
    wallet_id: UUID
    amount: Decimal
    balance_after: Decimal
    description: Optional[str] = None
    idempotency_key: Optional[str] = None
    created_at: datetime

    model_config = {"from_attributes": True}


class TransactionResponse(BaseModel):
    """Standard response for all mutating wallet operations."""
    status: str = "success"
    reference_id: UUID
    transaction_type: str
    amount: Decimal
    balance_after: Decimal
    message: str


class TransactionListResponse(BaseModel):
    account_id: UUID
    asset_type: str
    transactions: List[TransactionOut]
    total: int



class ErrorResponse(BaseModel):
    status: str = "error"
    code: str
    message: str
    details: Optional[str] = None


class RegisterRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=150)
    email: Optional[str] = Field(None, max_length=255)
    password: str = Field(..., min_length=6)


class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    account_id: UUID
    username: str
