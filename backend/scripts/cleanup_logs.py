"""
日志清理 CLI 工具
==================

定期清理 backend/utils/logs/ 下的过期日志文件。

修复: 6-16 体积审计发现 utils/logs/ 累积 12MB / 713 个文件。
      logger.py 的 DailyRotatingFileHandler 现在已经自带保留策略(retain_days=30),
      本 CLI 是独立保险,可被 cron 12 小时调一次。

用法:
    # 默认(保留 30 天)
    python -m scripts.cleanup_logs

    # 自定义保留天数
    python -m scripts.cleanup_logs --retain-days 7

    # dry-run: 看会删多少,不真删
    python -m scripts.cleanup_logs --dry-run

    # 写到非默认目录
    python -m scripts.cleanup_logs --log-dir /var/log/wc-analytics

典型 cron 配置 (/etc/cron.d/wc-analytics-cleanup):
    0 */12 * * * www-data cd /app/backend && python3 -m scripts.cleanup_logs

设计要点:
    - 只清理匹配 YYYY-MM-DD.log 格式的文件(不影响其它)
    - 误删保护: 解析不出的文件不动
    - 单次扫描,删除失败的继续下一个
    - 输出保留/删除统计
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

# ── 路径注入 ──
_CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
_BACKEND_ROOT = os.path.dirname(_CURRENT_DIR)
if _BACKEND_ROOT not in sys.path:
    sys.path.insert(0, _BACKEND_ROOT)

# utils/logs 是 DailyRotatingFileHandler 写日志的位置
DEFAULT_LOG_DIR = os.path.join(_BACKEND_ROOT, "utils", "logs")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="WC Analytics 日志清理 CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  %(prog)s                       # 默认 30 天保留
  %(prog)s --retain-days 7       # 严格 7 天
  %(prog)s --dry-run             # 只看不删
  %(prog)s --json                # JSON 输出(CI 集成)
        """,
    )
    p.add_argument(
        "--log-dir",
        default=DEFAULT_LOG_DIR,
        help=f"日志目录(默认: {DEFAULT_LOG_DIR})",
    )
    p.add_argument(
        "--retain-days",
        type=int,
        default=30,
        help="保留天数(默认: 30)",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="dry-run: 只统计,不删",
    )
    p.add_argument(
        "--json",
        action="store_true",
        help="输出 JSON 格式",
    )
    return p.parse_args()


def main() -> int:
    args = parse_args()
    started = datetime.utcnow().isoformat()

    log_dir = Path(args.log_dir)
    threshold = datetime.now() - timedelta(days=args.retain_days)

    # 文件名格式: <name>.YYYY-MM-DD.log 或 <name>.error.YYYY-MM-DD.log
    pattern = re.compile(r"^[^/]+\.(\d{4}-\d{2}-\d{2})\.log$")

    stats = {
        "scanned": 0,
        "kept": 0,
        "removed": 0,
        "errors": 0,
        "freed_bytes": 0,
        "removed_files": [],
    }

    if not log_dir.exists():
        msg = f"log_dir 不存在: {log_dir}"
        if args.json:
            print(json.dumps({"status": "skipped", "reason": msg,
                              "started_at": started}, ensure_ascii=False, indent=2))
        else:
            print(f"WARN: {msg}")
        return 0

    for f in sorted(log_dir.iterdir()):
        if not f.is_file():
            continue
        stats["scanned"] += 1
        m = pattern.match(f.name)
        if not m:
            stats["kept"] += 1
            continue
        try:
            file_date = datetime.strptime(m.group(1), "%Y-%m-%d")
        except ValueError:
            stats["kept"] += 1
            continue

        if file_date >= threshold:
            stats["kept"] += 1
            continue

        # 过期,准备删除
        size = f.stat().st_size
        if args.dry_run:
            stats["removed"] += 1
            stats["freed_bytes"] += size
            stats["removed_files"].append({
                "name": f.name, "size": size, "dry_run": True,
            })
            continue

        try:
            f.unlink()
            stats["removed"] += 1
            stats["freed_bytes"] += size
            stats["removed_files"].append({
                "name": f.name, "size": size, "dry_run": False,
            })
        except OSError as e:
            stats["errors"] += 1
            stats["removed_files"].append({
                "name": f.name, "size": size, "error": str(e),
            })

    finished = datetime.utcnow().isoformat()
    result = {
        "status": "ok",
        "log_dir": str(log_dir),
        "retain_days": args.retain_days,
        "dry_run": args.dry_run,
        "freed_mb": round(stats["freed_bytes"] / 1024 / 1024, 2),
        "started_at": started,
        "finished_at": finished,
        "stats": {k: v for k, v in stats.items() if k != "removed_files"},
        "removed_files": stats["removed_files"][:50],
    }

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] 扫描: {stats['scanned']} 个文件")
        print(f"[{datetime.now().strftime('%H:%M:%S')}] 保留: {stats['kept']}")
        print(f"[{datetime.now().strftime('%H:%M:%S')}] 删除: {stats['removed']} ({result['freed_mb']} MB)")
        if stats["errors"]:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] 失败: {stats['errors']}")
        if args.dry_run:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] dry-run 模式,未实际删除")

    return 0


if __name__ == "__main__":
    try:
        rc = main()
    except KeyboardInterrupt:
        print("\n中断", file=sys.stderr)
        sys.exit(130)
    except Exception as e:  # noqa: BLE001
        print(f"FATAL: {e}", file=sys.stderr)
        sys.exit(1)
    sys.exit(rc)
