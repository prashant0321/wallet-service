"""
Custom exceptions for the Wallet Service.
"""


class WalletServiceError(Exception):
    """Base class for all wallet service errors."""
    pass


class InsufficientFundsError(WalletServiceError):
    """Raised when a spend request exceeds the wallet's available balance."""
    def __init__(self, balance: float, requested: float, asset_symbol: str = "credits"):
        self.balance = balance
        self.requested = requested
        self.asset_symbol = asset_symbol
        super().__init__(
            f"Insufficient funds: wallet has {balance} {asset_symbol}, "
            f"but {requested} {asset_symbol} were requested."
        )


class WalletNotFoundError(WalletServiceError):
    """Raised when a wallet record cannot be found for the given account + asset."""
    def __init__(self, account_id: str, asset_type_id: str):
        super().__init__(
            f"No wallet found for account={account_id}, asset_type={asset_type_id}. "
            "Ensure the account exists and has been initialized."
        )


class AccountNotFoundError(WalletServiceError):
    """Raised when the requested account does not exist."""
    def __init__(self, account_id: str):
        super().__init__(f"Account not found: {account_id}")


class AssetTypeNotFoundError(WalletServiceError):
    """Raised when the requested asset type does not exist or is inactive."""
    def __init__(self, asset_type_id: str):
        super().__init__(f"Asset type not found or inactive: {asset_type_id}")


class IdempotencyConflictError(WalletServiceError):
    """
    Raised when an idempotency key has already been used for a *different* request body.
    This indicates a programming error on the caller side.
    """
    def __init__(self, key: str):
        self.key = key
        super().__init__(
            f"Idempotency key '{key}' was already used with a different request payload."
        )


class DuplicateIdempotentRequestError(WalletServiceError):
    """
    Raised (internally) when a request is a valid duplicate — same key, same payload.
    The cached response should be returned to the caller.
    """
    def __init__(self, key: str, cached_response: dict):
        self.key = key
        self.cached_response = cached_response
        super().__init__(f"Duplicate idempotent request for key '{key}' — returning cached response.")


class NegativeBalanceError(WalletServiceError):
    """Safety net: raised if a transaction would push a balance below zero."""
    def __init__(self, wallet_id: str, resulting_balance: float):
        super().__init__(
            f"Transaction rejected: wallet {wallet_id} would have a negative balance "
            f"of {resulting_balance}. This is a data-integrity violation."
        )
