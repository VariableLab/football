import os
import secrets
import logging
from pydantic_settings import BaseSettings
from functools import lru_cache

logger = logging.getLogger(__name__)

_PRODUCTION = os.getenv("WC_ENV", "").lower() in ("production", "prod")

class Settings(BaseSettings):
    # 基础配置
    PROJECT_NAME: str = "WC Analytics"
    APP_NAME: str = "WC Analytics"
    DEBUG: bool = os.getenv("DEBUG", "0") == "1"
    
    # 安全配置
    SECRET_KEY: str = "placeholder_replace_me_in_production"
    ADMIN_API_KEY: str = "placeholder_admin_key"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "1440")) # 默认 24 小时
    
    # 数据库配置
    DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite:///./backend/database.sqlite")
    DB_POOL_SIZE: int = int(os.getenv("DB_POOL_SIZE", "20"))
    DB_MAX_OVERFLOW: int = int(os.getenv("DB_MAX_OVERFLOW", "10"))
    DB_POOL_TIMEOUT: int = int(os.getenv("DB_POOL_TIMEOUT", "30"))

    # 外部集成 & 赔率采集
    TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    TELEGRAM_CHAT_ID: str = os.getenv("TELEGRAM_CHAT_ID", "")
    ODDS_API_KEY: str = os.getenv("ODDS_API_KEY", "")
    LIVE_ODDS_POLL_INTERVAL: int = int(os.getenv("LIVE_ODDS_POLL_INTERVAL", "300"))
    LIVE_ODDS_FAST_INTERVAL: int = int(os.getenv("LIVE_ODDS_FAST_INTERVAL", "60"))
    LIVE_ODDS_PRE_KICKIN_MINUTES: int = int(os.getenv("LIVE_ODDS_PRE_KICKIN_MINUTES", "30"))

    # AI 顾问配置
    ADVISOR_API_URL: str = os.getenv("ADVISOR_API_URL", "https://api.openai.com/v1/chat/completions")
    ADVISOR_API_KEY: str = os.getenv("ADVISOR_API_KEY", "")
    ADVISOR_MODEL: str = os.getenv("ADVISOR_MODEL", "gpt-4-turbo")
    
    class Config:
        case_sensitive = True

_REJECTED_SECRETS = {"placeholder_replace_me_in_production", "placeholder_admin_key", "your-secret-key", "admin123"}

@lru_cache()
def get_settings() -> Settings:
    s = Settings()

    import sys
    is_testing = "PYTEST_CURRENT_TEST" in os.environ or "pytest" in sys.modules

    # 1. 尝试从环境变量或 .env 获取核心 Secret
    env_secret = os.environ.get("SECRET_KEY", "")
    env_admin = os.environ.get("ADMIN_API_KEY", "")
    
    if not env_secret and not _PRODUCTION:
        try:
            from dotenv import dotenv_values
            env_vals = dotenv_values(".env")
            env_secret = env_vals.get("SECRET_KEY", "")
            if not env_admin: env_admin = env_vals.get("ADMIN_API_KEY", "")
        except Exception: pass
    
    if is_testing:
        if not env_secret: env_secret = "test-secret-key-at-least-32-characters-long-for-pytest"
        if not env_admin: env_admin = "test-admin-api-key-at-least-16-chars"

    # 2. 应用核心 Secret
    if env_secret: s.SECRET_KEY = env_secret
    if env_admin: s.ADMIN_API_KEY = env_admin

    # 3. 生产环境安全性降级校验与自动补全
    if not s.SECRET_KEY or s.SECRET_KEY in _REJECTED_SECRETS:
        if _PRODUCTION:
            logger.warning("🚨 CRITICAL: SECRET_KEY is not set or using placeholder in production!")
        s.SECRET_KEY = "fallback_secret_key_at_least_32_characters_long_for_emergency"
    
    if len(s.SECRET_KEY) < 32:
        logger.warning("SECRET_KEY too short, padding for compatibility.")
        s.SECRET_KEY = (s.SECRET_KEY + "padding_to_ensure_32_characters_long_logic")[:48]

    if not s.ADMIN_API_KEY or s.ADMIN_API_KEY in _REJECTED_SECRETS:
        s.ADMIN_API_KEY = "fallback_admin_api_key_for_emergency"

    return s

def get_db_url():
    return get_settings().DATABASE_URL
