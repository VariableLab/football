"""
调度器共享工具 — DBSession、cleanup_old_backups
供其他 scheduler 子模块导入使用。
"""
import os
import re
from datetime import datetime


class DBSession:
    """上下文管理器，确保调度器任务中的数据库会话正确关闭"""
    def __enter__(self):
        from database.models import SessionLocal
        self.db = SessionLocal()
        return self.db

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type:
            self.db.rollback()
        else:
            self.db.commit()
        self.db.close()
        return False


# Backend root path resolution
try:
    from database.config import get_settings, _BACKEND_ROOT
except Exception:
    _BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def cleanup_old_backups(backup_dir: str, keep_daily: int = 7, keep_weekly: int = 4, max_size_gb: float = 5.0):
    """按保留策略清理旧备份。"""
    import os
    from datetime import datetime

    pattern = re.compile(r"db_(\d{8})_(\d{6})\.sqlite$")
    files = []
    for f in os.listdir(backup_dir):
        m = pattern.match(f)
        if not m:
            continue
        path = os.path.join(backup_dir, f)
        if not os.path.isfile(path):
            continue
        try:
            ts = datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S")
        except ValueError:
            continue
        files.append((ts, path, os.path.getsize(path)))

    files.sort(reverse=True)  # 最新优先
    to_keep = set()

    # 1) 日备: 连续 keep_daily 天,每天保留 1 个
    seen_dates = set()
    daily_count = 0
    for ts, path, _ in files:
        d = ts.date()
        if d in seen_dates:
            continue
        seen_dates.add(d)
        if daily_count >= keep_daily:
            break
        to_keep.add(path)
        daily_count += 1

    # 2) 周备: 周日的备份额外保留 keep_weekly 周
    seen_weeks = set()
    weekly_count = 0
    for ts, path, _ in files:
        if ts.weekday() != 6:  # 6 = Sunday
            continue
        iso_week = ts.isocalendar()[:2]
        if iso_week in seen_weeks:
            continue
        seen_weeks.add(iso_week)
        if weekly_count >= keep_weekly:
            break
        to_keep.add(path)
        weekly_count += 1

    # 3) 大小: 超过 max_size_gb 删最旧
    total_size = sum(s for _, _, s in files)
    max_bytes = int(max_size_gb * 1024 ** 3)
    for ts, path, size in reversed(files):
        if total_size <= max_bytes:
            break
        try:
            os.remove(path)
            total_size -= size
            if path in to_keep:
                to_keep.remove(path)
        except OSError as e:
            pass

    # 4) 删除不在 to_keep 集合的
    removed = 0
    for ts, path, size in files:
        if path in to_keep:
            continue
        if not os.path.exists(path):
            continue
        try:
            os.remove(path)
            removed += 1
        except OSError as e:
            pass

    if removed:
        import logging
        logging.getLogger("scheduler.external_sync").info(f"[backup] cleanup: removed {removed} old files, keep {len(to_keep)}")
