class WalletServiceError(Exception):
    pass


class InsufficientFundsError(WalletServiceError):
    def __init__(self, balance: float, requested: float, asset_symbol: str = "credits"):
        self.balance = balance
        self.requested = requested
        self.asset_symbol = asset_symbol
        super().__init__(
            f"Insufficient funds: wallet has {balance} {asset_symbol}, "
            f"but {requested} {asset_symbol} were requested."
        )


class WalletNotFoundError(WalletServiceError):
    def __init__(self, account_id: str, asset_type_id: str):
        super().__init__(
            f"No wallet found for account={account_id}, asset_type={asset_type_id}."
        )


class AccountNotFoundError(WalletServiceError):
    def __init__(self, account_id: str):
        super().__init__(f"Account not found: {account_id}")


class AssetTypeNotFoundError(WalletServiceError):
    def __init__(self, asset_type_id: str):
        super().__init__(f"Asset type not found or inactive: {asset_type_id}")


class IdempotencyConflictError(WalletServiceError):
    def __init__(self, key: str):
        self.key = key
        super().__init__(
            f"Idempotency key '{key}' was already used with a different request payload."
        )


class DuplicateIdempotentRequestError(WalletServiceError):
    def __init__(self, key: str, cached_response: dict):
        self.key = key
        self.cached_response = cached_response
        super().__init__(f"Duplicate idempotent request for key '{key}'.")


class NegativeBalanceError(WalletServiceError):
    def __init__(self, wallet_id: str, resulting_balance: float):
        super().__init__(
            f"Transaction rejected: wallet {wallet_id} would have a negative balance of {resulting_balance}."
        )
