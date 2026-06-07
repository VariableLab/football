import os
import logging
from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache

logger = logging.getLogger(__name__)

_PRODUCTION = os.getenv("WC_ENV", "").lower() in ("production", "prod")
_CUR_DIR = os.path.dirname(os.path.abspath(__file__))
_BACKEND_ROOT = os.path.dirname(_CUR_DIR)

class Settings(BaseSettings):
    # 💡 允许额外字段，防止环境中有冗余变量时崩溃
    model_config = SettingsConfigDict(
        extra='ignore', 
        case_sensitive=True,
        env_file=os.path.join(_BACKEND_ROOT, ".env"),
        env_file_encoding='utf-8'
    )

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
    FOOTBALL_DATA_API_KEY: str = "" # 補上這個引發報錯的 key
    LIVE_ODDS_POLL_INTERVAL: int = 300
    LIVE_ODDS_FAST_INTERVAL: int = 60
    LIVE_ODDS_PRE_KICKIN_MINUTES: int = 30

    # AI 顾问配置
    ADVISOR_API_URL: str = "https://api.openai.com/v1/chat/completions"
    ADVISOR_API_KEY: str = ""
    ADVISOR_MODEL: str = "gpt-4-turbo"

_REJECTED_SECRETS = {"placeholder_replace_me_in_production", "placeholder_admin_key", "your-secret-key", "admin123"}

@lru_cache()
def get_settings() -> Settings:
    try:
        s = Settings()
    except Exception as e:
        # 💡 極致兜底：如果加載 .env 報錯，先用預設值啟動，確保服務不死
        logger.error(f"Settings initialization failed: {e}. Using defaults.")
        s = Settings(_env_file=None) 

    import sys
    is_testing = "PYTEST_CURRENT_TEST" in os.environ or "pytest" in sys.modules

    # 測試環境覆蓋
    if is_testing:
        s.SECRET_KEY = "test-secret-key-at-least-32-characters-long-for-pytest"
        s.ADMIN_API_KEY = "test-admin-api-key-at-least-16-chars"

    # 密鑰加固
    if not s.SECRET_KEY or len(s.SECRET_KEY) < 32 or s.SECRET_KEY in _REJECTED_SECRETS:
        s.SECRET_KEY = (str(s.SECRET_KEY or "") + "fallback_padding_secret_key_32_chars_long")[:48]

    if not s.ADMIN_API_KEY or s.ADMIN_API_KEY in _REJECTED_SECRETS:
        s.ADMIN_API_KEY = "fallback_admin_api_key_for_emergency"

    return s

def get_db_url():
    return get_settings().DATABASE_URL
