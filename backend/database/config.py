import os
import secrets
import logging
from pydantic_settings import BaseSettings
from functools import lru_cache

logger = logging.getLogger(__name__)

_PRODUCTION = os.getenv("WC_ENV", "").lower() in ("production", "prod")


def _generate_secret() -> str:
    """生成安全的随机密钥（仅在未设置环境变量时使用）"""
    return secrets.token_urlsafe(32)


class Settings(BaseSettings):
    # App
    APP_NAME: str = "WC Analytics"
    DEBUG: bool = False
    SECRET_KEY: str = _generate_secret()

    # Database
    DATABASE_URL: str = "postgresql://postgre:prefect@129.146.124.72:5432/wcanalytics"
    DB_POOL_SIZE: int = 20
    DB_MAX_OVERFLOW: int = 40
    DB_POOL_TIMEOUT: int = 60

    # JWT
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 7  # 7 days

    # Stripe (optional)
    STRIPE_SECRET_KEY: str = ""
    STRIPE_WEBHOOK_SECRET: str = ""
    STRIPE_PRICE_ID: str = ""

    # Odds API (the-odds-api.com)
    ODDS_API_KEY: str = ""

    # football-data.org API
    FOOTBALL_DATA_API_KEY: str = ""

    # API-Football (api-football.com)
    API_FOOTBALL_KEY: str = ""

    # Live odds polling intervals (seconds)
    LIVE_ODDS_POLL_INTERVAL: int = 30
    LIVE_ODDS_FAST_INTERVAL: int = 10
    LIVE_HEDGE_CHECK_INTERVAL: int = 30
    LIVE_ODDS_PRE_KICKIN_MINUTES: int = 10

    # Admin
    ADMIN_API_KEY: str = _generate_secret()

    # AI Advisor (Deepstock)
    ADVISOR_API_URL: str = "https://deepstock.zone.id/v1/chat/completions"
    ADVISOR_API_KEY: str = "sk-F9fKBTHbEJE4AEY9aOy5IwwUdxOPrZ3NilvAnohOO1ODm1KT"
    ADVISOR_MODEL: str = "z-ai/glm-5.1"

    # Telegram Bot
    TELEGRAM_BOT_TOKEN: str = "8815524049:AAEoShNB5hRVHLQOVYGf8hzP05PNQ4ibb9I"
    TELEGRAM_CHAT_ID: str = "-5176302899" 

    class Config:
        env_file = ".env" if not _PRODUCTION else ""
        env_file_encoding = "utf-8"


_REJECTED_SECRETS = {
    "change-me-in-production-32chars-long!!",
    "your-super-secret-32-char-key-here!!",
    "change-me-in-production",
    "your-admin-api-key-change-me",
}


def _check_secret_source(key_name: str) -> bool:
    """Check if secret comes from OS environment (not .env file)."""
    return key_name in os.environ


@lru_cache()
def get_settings() -> Settings:
    s = Settings()

    if _PRODUCTION:
        logger.info("Production mode: .env file disabled, secrets must come from environment variables")

    # Validate SECRET_KEY
    env_secret = os.environ.get("SECRET_KEY", "")
    import sys
    is_testing = "PYTEST_CURRENT_TEST" in os.environ or "pytest" in sys.modules
    if not env_secret and not _PRODUCTION:
        try:
            from dotenv import dotenv_values
            env_vals = dotenv_values(".env")
            env_secret = env_vals.get("SECRET_KEY", "")
        except Exception:
            pass
    
    # In testing environment, provide a default secret key if not configured
    if not env_secret and is_testing:
        env_secret = "test-secret-key-at-least-32-characters-long-for-pytest"
        
    if not env_secret:
        raise ValueError(
            "SECRET_KEY is not configured. Every restart generates a new random key, "
            "which invalidates all existing JWT tokens. "
            "Set via environment variable: "
            "python3 -c \"import secrets; print(secrets.token_urlsafe(48))\""
        )
    if _PRODUCTION and not _check_secret_source("SECRET_KEY"):
        raise ValueError(
            "Production requires SECRET_KEY from environment variable, not .env file. "
            "Set WC_ENV=production and export SECRET_KEY directly."
        )

    if not s.SECRET_KEY or len(s.SECRET_KEY) < 32:
        raise ValueError("SECRET_KEY must be at least 32 characters.")
    if s.SECRET_KEY in _REJECTED_SECRETS:
        raise ValueError(
            "SECRET_KEY must not be a placeholder. "
            "Generate one with: python3 -c \"import secrets; print(secrets.token_urlsafe(48))\""
        )

    # Validate ADMIN_API_KEY
    env_admin = os.environ.get("ADMIN_API_KEY", "")
    if not env_admin and not _PRODUCTION:
        try:
            from dotenv import dotenv_values
            env_vals = dotenv_values(".env")
            env_admin = env_vals.get("ADMIN_API_KEY", "")
        except Exception:
            pass
            
    if not env_admin and is_testing:
        env_admin = "test-admin-api-key-at-least-16-chars"
        
    if not env_admin:
        raise ValueError(
            "ADMIN_API_KEY is not configured. Without a persistent key, "
            "you will lose admin access on every restart. "
            "Set via environment variable: "
            "python3 -c \"import secrets; print(secrets.token_urlsafe(32))\""
        )
    
    # Update settings with valid keys for testing
    if is_testing:
        s.SECRET_KEY = env_secret
        s.ADMIN_API_KEY = env_admin

    if _PRODUCTION and not _check_secret_source("ADMIN_API_KEY"):
        raise ValueError(
            "Production requires ADMIN_API_KEY from environment variable, not .env file. "
            "Set WC_ENV=production and export ADMIN_API_KEY directly."
        )

    if not s.ADMIN_API_KEY or len(s.ADMIN_API_KEY) < 16:
        raise ValueError("ADMIN_API_KEY must be at least 16 characters.")
    if s.ADMIN_API_KEY in _REJECTED_SECRETS:
        raise ValueError(
            "ADMIN_API_KEY must not be a placeholder. "
            "Generate one with: python3 -c \"import secrets; print(secrets.token_urlsafe(32))\""
        )

    return s
