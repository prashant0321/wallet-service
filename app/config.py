"""
Application configuration loaded from environment variables.
"""
from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    DATABASE_URL: str = "sqlite://"
    DB_ECHO: bool = False          

    APP_NAME: str = "Wallet Service"
    APP_VERSION: str = "1.0.0"
    DEBUG: bool = False

    IDEMPOTENCY_KEY_TTL_HOURS: int = 24

    JWT_SECRET: str = "change-me-in-production"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


@lru_cache()
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
