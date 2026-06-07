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
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 1440
    
    # 数据库配置
    DATABASE_URL: str = "sqlite:///./backend/database.sqlite"
    DB_POOL_SIZE: int = 20
    DB_MAX_OVERFLOW: int = 10
    DB_POOL_TIMEOUT: int = 30

    # 外部集成 & 赔率采集
    TELEGRAM_BOT_TOKEN: str = ""
    TELEGRAM_CHAT_ID: str = ""
    ODDS_API_KEY: str = ""
    LIVE_ODDS_POLL_INTERVAL: int = 300
    LIVE_ODDS_FAST_INTERVAL: int = 60
    LIVE_ODDS_PRE_KICKIN_MINUTES: int = 30

    # AI 顾问配置
    ADVISOR_API_URL: str = "https://api.openai.com/v1/chat/completions"
    ADVISOR_API_KEY: str = ""
    ADVISOR_MODEL: str = "gpt-4-turbo"
    
    class Config:
        case_sensitive = True
        env_file = ".env" # 💡 关键：允许从 .env 自动加载所有字段

_REJECTED_SECRETS = {"placeholder_replace_me_in_production", "placeholder_admin_key", "your-secret-key", "admin123"}

@lru_cache()
def get_settings() -> Settings:
    # 1. Pydantic 会自动按优先级加载：ENV > .env > Defaults
    s = Settings()

    import sys
    is_testing = "PYTEST_CURRENT_TEST" in os.environ or "pytest" in sys.modules

    # 2. 测试环境硬性覆盖
    if is_testing:
        s.SECRET_KEY = "test-secret-key-at-least-32-characters-long-for-pytest"
        s.ADMIN_API_KEY = "test-admin-api-key-at-least-16-chars"

    # 3. 生产环境安全性降级校验与自动补全
    if not s.SECRET_KEY or s.SECRET_KEY in _REJECTED_SECRETS:
        if _PRODUCTION:
            logger.warning("🚨 CRITICAL: SECRET_KEY is not set or using placeholder in production!")
        s.SECRET_KEY = "fallback_secret_key_at_least_32_characters_long_for_emergency"
    
    if len(s.SECRET_KEY) < 32:
        s.SECRET_KEY = (s.SECRET_KEY + "padding_to_ensure_32_characters_long_logic")[:48]

    if not s.ADMIN_API_KEY or s.ADMIN_API_KEY in _REJECTED_SECRETS:
        s.ADMIN_API_KEY = "fallback_admin_api_key_for_emergency"

    return s

def get_db_url():
    return get_settings().DATABASE_URL
