"""
WC Analytics — 统一日志系统

功能：
  - 分级日志（DEBUG/INFO/WARNING/ERROR/CRITICAL）
  - 按日期自动轮转文件
  - 结构化 JSON 输出（便于后续分析）
  - 控制台彩色输出
  - 按模块隔离日志文件

用法：
    from logger import get_logger
    logger = get_logger("prediction")
    logger.info("预测完成", extra={"match_id": 123, "confidence": 0.85})
"""

import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

# ────────────────────────────
# 配置
# ────────────────────────────
LOG_DIR = Path(__file__).parent / "logs"
LOG_DIR.mkdir(exist_ok=True)

# 日志级别映射
LOG_LEVEL = logging.INFO  # 生产环境可改为 WARNING

# 彩色输出
COLORS = {
    "DEBUG": "\033[36m",      # 青色
    "INFO": "\033[32m",       # 绿色
    "WARNING": "\033[33m",    # 黄色
    "ERROR": "\033[31m",      # 红色
    "CRITICAL": "\033[35m",   # 紫色
    "RESET": "\033[0m",
}


# ────────────────────────────
# 格式化器
# ────────────────────────────
class ColoredFormatter(logging.Formatter):
    """控制台彩色格式化器"""

    def format(self, record: logging.LogRecord) -> str:
        color = COLORS.get(record.levelname, COLORS["RESET"])
        reset = COLORS["RESET"]
        timestamp = datetime.fromtimestamp(record.created).strftime("%H:%M:%S")
        return f"{color}[{timestamp}] [{record.levelname:8}] [{record.name}]{reset} {record.getMessage()}"


class JsonFormatter(logging.Formatter):
    """JSON 结构化格式化器（用于文件）"""

    def format(self, record: logging.LogRecord) -> str:
        log_obj: Dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }

        # 合并 extra 字段
        if hasattr(record, "extra_data"):
            log_obj.update(record.extra_data)

        # 异常信息
        if record.exc_info:
            log_obj["exception"] = self.formatException(record.exc_info)

        return json.dumps(log_obj, ensure_ascii=False, default=str)


# ────────────────────────────
# 处理器
# ────────────────────────────
class DailyRotatingFileHandler(logging.FileHandler):
    """按日期自动轮转的文件处理器"""

    def __init__(self, log_dir: Path, name: str):
        self.log_dir = log_dir
        self._handler_name = name
        self.current_date = datetime.now().strftime("%Y-%m-%d")
        super().__init__(self._get_path(), encoding="utf-8")

    def _get_path(self) -> str:
        return str(self.log_dir / f"{self._handler_name}.{self.current_date}.log")

    def emit(self, record: logging.LogRecord):
        # 检查是否需要切换日期
        new_date = datetime.now().strftime("%Y-%m-%d")
        if new_date != self.current_date:
            self.current_date = new_date
            self.baseFilename = self._get_path()
            self.stream.close()
            self.stream = self._open()
        super().emit(record)


# ────────────────────────────
# 日志工厂
# ────────────────────────────
_loggers: Dict[str, logging.Logger] = {}


def get_logger(name: str, level: Optional[int] = None) -> logging.Logger:
    """
    获取指定名称的日志器。
    每个名称对应独立的日志文件。
    """
    if name in _loggers:
        return _loggers[name]

    logger = logging.getLogger(name)
    logger.setLevel(level or LOG_LEVEL)
    logger.propagate = False  # 避免重复输出

    # 避免重复添加处理器
    if logger.handlers:
        return logger

    # 1. 控制台处理器（彩色）
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.DEBUG)
    console_handler.setFormatter(ColoredFormatter())
    logger.addHandler(console_handler)

    # 2. 文件处理器（JSON 结构化）
    file_handler = DailyRotatingFileHandler(LOG_DIR, name)
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(JsonFormatter())
    logger.addHandler(file_handler)

    # 3. 错误专用文件（ERROR 及以上）
    error_handler = DailyRotatingFileHandler(LOG_DIR, f"{name}.error")
    error_handler.setLevel(logging.ERROR)
    error_handler.setFormatter(JsonFormatter())
    logger.addHandler(error_handler)

    _loggers[name] = logger
    return logger


# ────────────────────────────
# 便捷方法
# ────────────────────────────
def log_with_extra(logger: logging.Logger, level: int, msg: str, **kwargs):
    """带结构化数据的日志记录"""
    extra = {"extra_data": kwargs}
    logger.log(level, msg, extra=extra)


# ────────────────────────────
# 全局异常捕获
# ────────────────────────────
def setup_global_exception_handler():
    """捕获未处理的异常并记录"""
    _logger = get_logger("uncaught")

    def handle_exception(exc_type, exc_value, exc_traceback):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_traceback)
            return
        _logger.critical(
            "Uncaught exception",
            exc_info=(exc_type, exc_value, exc_traceback),
        )

    sys.excepthook = handle_exception


# 启动时自动设置
setup_global_exception_handler()


# ────────────────────────────
# 测试
# ────────────────────────────
if __name__ == "__main__":
    print("=" * 60)
    print("日志系统测试")
    print("=" * 60)

    # 测试不同模块的日志器
    for name in ["main", "prediction", "odds", "auth", "scheduler"]:
        log = get_logger(name)
        log.debug("调试信息", extra={"extra_data": {"test": True}})
        log.info("普通信息", extra={"extra_data": {"match_id": 123}})
        log.warning("警告信息", extra={"extra_data": {"old_odds": 2.5, "new_odds": 2.8}})
        log.error("错误信息", extra={"extra_data": {"error_code": "E001"}})

    print(f"\n日志文件已写入: {LOG_DIR}")
    print(f"文件列表:")
    for f in sorted(LOG_DIR.glob("*.log")):
        print(f"  {f.name}")
