"""
Application configuration loaded from environment variables.
"""
from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    # ── Database ────────────────────────────────────────────────────────────
    # Default: SQLite (built-in, zero setup). Switch to PostgreSQL in .env for production.
    DATABASE_URL: str = "sqlite:///./wallet.db"
    DB_ECHO: bool = False          # Set True to log all SQL (dev only)

    # ── Application ─────────────────────────────────────────────────────────
    APP_NAME: str = "Wallet Service"
    APP_VERSION: str = "1.0.0"
    DEBUG: bool = False

    # ── Idempotency ──────────────────────────────────────────────────────────
    IDEMPOTENCY_KEY_TTL_HOURS: int = 24   # Keys expire after 24 h

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


@lru_cache()
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
