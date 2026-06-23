import os
import logging
from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache

logger = logging.getLogger(__name__)

# 💡 物理定位 backend 根目錄，確保不論在哪執行都能找到 .env
_CUR_DIR = os.path.dirname(os.path.abspath(__file__))
_BACKEND_ROOT = os.path.dirname(_CUR_DIR)
_ENV_PATH = os.path.join(_BACKEND_ROOT, ".env")

class Settings(BaseSettings):
    # 💡 使用絕對路徑加載 .env，且忽略額外字段
    model_config = SettingsConfigDict(
        extra='ignore', 
        case_sensitive=True,
        env_file=_ENV_PATH,
        env_file_encoding='utf-8'
    )

    # 基础配置
    PROJECT_NAME: str = "WC Analytics"
    APP_NAME: str = "WC Analytics"
    DEBUG: bool = False
    TEST_MODE: bool = False
    
    # 安全配置 — 生产环境必须设置,无回退默认值
    SECRET_KEY: str = ""
    ADMIN_API_KEY: str = ""
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 1440
    
    # 数据库配置
    DATABASE_URL: str = os.environ.get("DATABASE_URL", "postgresql://postgre:prefect@129.146.124.72:5432/football")
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
    # 💡 移除 os.chdir，改為純淨的配置讀取
    s = Settings()

    # 💡 强行同步到 os.environ，解决第三方模块或特定 router 通过 os.getenv 读不到 Pydantic env 的问题
    os.environ["TEST_MODE"] = str(s.TEST_MODE).lower()

    import sys
    is_testing = "PYTEST_CURRENT_TEST" in os.environ or "pytest" in sys.modules

    if is_testing:
        s.SECRET_KEY = "test-secret-key-at-least-32-characters-long-for-pytest"
        s.ADMIN_API_KEY = "test-admin-api-key-at-least-16-chars"
        # 💡 测试环境下使用内存数据库，避免文件路径问题导致 unable to open database file
        s.DATABASE_URL = "sqlite:///:memory:"
    else:
        # 💡 生产环境: 强制要求密钥已配置
        if not s.SECRET_KEY or s.SECRET_KEY.startswith("fallback"):
            raise RuntimeError(
                "SECRET_KEY is not configured. "
                "Set the SECRET_KEY environment variable before starting the application."
            )
        if not s.ADMIN_API_KEY:
            raise RuntimeError(
                "ADMIN_API_KEY is not configured. "
                "Set the ADMIN_API_KEY environment variable before starting the application."
            )

    # 确保 Key 长度符合加密要求
    if len(s.SECRET_KEY) < 32:
        s.SECRET_KEY = (str(s.SECRET_KEY) + "padding_logic_to_ensure_32_chars_long")[:48]

    # 💡 强制 SQLite 物理文件使用绝对路径，避免 CWD 漂移导致 unable to open database file
    if s.DATABASE_URL.startswith("sqlite:///./backend/"):
        db_filename = s.DATABASE_URL.replace("sqlite:///./backend/", "")
        abs_path = os.path.join(_BACKEND_ROOT, db_filename)
        s.DATABASE_URL = f"sqlite:///{abs_path}"
        
    return s

def get_db_url():
    return get_settings().DATABASE_URL
