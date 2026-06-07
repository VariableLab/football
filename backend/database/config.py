import os
import logging
from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache

logger = logging.getLogger(__name__)

class Settings(BaseSettings):
    # 💡 允许额外字段，彻底解决 ValidationError
    model_config = SettingsConfigDict(
        extra='ignore', 
        case_sensitive=True,
        env_file=".env",
        env_file_encoding='utf-8'
    )

    # 基础配置
    PROJECT_NAME: str = "WC Analytics"
    APP_NAME: str = "WC Analytics"
    DEBUG: bool = False
    
    # 安全配置
    SECRET_KEY: str = "fallback_secret_key_at_least_32_chars_long"
    ADMIN_API_KEY: str = "fallback_admin_key"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 1440
    
    # 数据库配置
    DATABASE_URL: str = "sqlite:///./backend/database.sqlite"
    DB_POOL_SIZE: int = 10
    DB_MAX_OVERFLOW: int = 5
    DB_POOL_TIMEOUT: int = 30

    # 外部集成
    TELEGRAM_BOT_TOKEN: str = ""
    TELEGRAM_CHAT_ID: str = ""
    ODDS_API_KEY: str = ""
    FOOTBALL_DATA_API_KEY: str = ""
    LIVE_ODDS_POLL_INTERVAL: int = 300
    LIVE_ODDS_FAST_INTERVAL: int = 60
    LIVE_ODDS_PRE_KICKIN_MINUTES: int = 30

    # AI 顾问配置
    ADVISOR_API_URL: str = "https://api.openai.com/v1/chat/completions"
    ADVISOR_API_KEY: str = ""
    ADVISOR_MODEL: str = "gpt-4-turbo"

@lru_cache()
def get_settings() -> Settings:
    # 强制进入 backend 目录寻找 .env
    _cur_dir = os.path.dirname(os.path.abspath(__file__))
    _backend_dir = os.path.dirname(_cur_dir)
    os.chdir(_backend_dir)
    
    s = Settings()
    
    import sys
    is_testing = "PYTEST_CURRENT_TEST" in os.environ or "pytest" in sys.modules
    if is_testing:
        s.SECRET_KEY = "test-secret-key-at-least-32-characters-long-for-pytest"
        s.ADMIN_API_KEY = "test-admin-api-key-at-least-16-chars"

    # 确保 Key 长度符合加密要求
    if len(s.SECRET_KEY) < 32:
        s.SECRET_KEY = (s.SECRET_KEY + "padding_logic_to_ensure_32_chars_long")[:48]

    return s

def get_db_url():
    return get_settings().DATABASE_URL
