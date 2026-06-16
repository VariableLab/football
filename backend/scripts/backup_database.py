"""
数据库备份 CLI 工具
====================

脱离 scheduler 单独运行的备份入口。
复用 `monitor.scheduler.cleanup_old_backups` 逻辑(同一份保留策略),
支持手动触发、dry-run、verbose、JSON 输出等 CLI 选项。

用法:
    # 默认备份(走 scheduler 的默认配置)
    python -m scripts.backup_database

    # 强制备份(即使哈希未变)
    python -m scripts.backup_database --force

    # 自定义保留策略
    python -m scripts.backup_database --keep-daily 14 --keep-weekly 8 --max-size-gb 10

    # dry-run: 仅打印将执行的动作,不实际写文件
    python -m scripts.backup_database --dry-run

    # 备份到不同目录
    python -m scripts.backup_database --backup-dir /data/backups

    # CI 集成: 输出 JSON
    python -m scripts.backup_database --json

设计要点:
    - 不重新实现 backup 逻辑,直接复用 scheduler 里的 cleanup_old_backups
    - 显式接收 backup_dir / db_path,避免硬编码相对路径
    - JSON 输出方便 CI / cron / 监控集成
    - exit code: 0=ok/dry_run, 1=failed, 2=skipped(状态正常但没做事)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime

# ── 路径注入: 让 `python -m scripts.backup_database` 找到 backend 根 ──
_CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
_BACKEND_ROOT = os.path.dirname(_CURRENT_DIR)
if _BACKEND_ROOT not in sys.path:
    sys.path.insert(0, _BACKEND_ROOT)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="WC Analytics 数据库备份工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  %(prog)s                                    # 走默认配置
  %(prog)s --force                            # 强制备份,跳过哈希去重
  %(prog)s --keep-daily 3 --max-size-gb 1.0   # 严格保留策略
  %(prog)s --dry-run -v                       # 只看不跑
  %(prog)s --backup-dir /var/backups/football  # 备份到独立目录
        """,
    )
    p.add_argument(
        "--db-path",
        default=os.path.join(_BACKEND_ROOT, "database.sqlite"),
        help="源数据库路径(默认: backend/database.sqlite)",
    )
    p.add_argument(
        "--backup-dir",
        default=os.path.join(_BACKEND_ROOT, "backup"),
        help="备份目录(默认: backend/backup)",
    )
    p.add_argument(
        "--keep-daily",
        type=int,
        default=7,
        help="日备保留天数(默认: 7)",
    )
    p.add_argument(
        "--keep-weekly",
        type=int,
        default=4,
        help="周备保留周数(默认: 4)",
    )
    p.add_argument(
        "--max-size-gb",
        type=float,
        default=5.0,
        help="备份目录总大小硬上限 GB(默认: 5.0)",
    )
    p.add_argument(
        "--force",
        action="store_true",
        help="强制备份(跳过哈希去重)",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="dry-run: 不实际写文件,只打印计划动作",
    )
    p.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="打印详细日志",
    )
    p.add_argument(
        "--json",
        action="store_true",
        help="输出 JSON 格式(便于 CI 集成)",
    )
    return p.parse_args()


def main() -> int:
    args = parse_args()

    # ── 延迟导入: 避免在 --help 时也加载重模块 ──
    from monitor.scheduler import cleanup_old_backups

    result: dict = {
        "started_at": datetime.now().isoformat(timespec="seconds"),
        "db_path": args.db_path,
        "backup_dir": args.backup_dir,
        "dry_run": args.dry_run,
        "force": args.force,
    }

    # ── 校验源 db ──
    if not os.path.exists(args.db_path):
        result["status"] = "failed"
        result["reason"] = f"db_not_found: {args.db_path}"
        _emit(args, "ERROR", f"db 不存在: {args.db_path}")
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1

    db_size_mb = os.path.getsize(args.db_path) / 1024 / 1024
    result["db_size_mb"] = round(db_size_mb, 2)
    _emit(args, "INFO", f"源 db: {args.db_path} ({db_size_mb:.1f} MB)")

    # ── 哈希去重判断 ──
    import hashlib
    try:
        with open(args.db_path, "rb") as f:
            db_hash = hashlib.md5(f.read()).hexdigest()[:12]
    except OSError as e:
        result["status"] = "failed"
        result["reason"] = f"hash_error: {e}"
        _emit(args, "ERROR", f"读取 db 哈希失败: {e}")
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1

    result["db_hash"] = db_hash

    meta_path = os.path.join(args.backup_dir, ".backup_meta.json")
    last_hash = None
    if os.path.exists(meta_path):
        try:
            with open(meta_path, "r") as f:
                meta = json.load(f)
                last_hash = meta.get("last_hash")
        except (OSError, json.JSONDecodeError):
            pass

    unchanged = (db_hash == last_hash) and not args.force
    if unchanged:
        result["status"] = "skipped"
        result["reason"] = "unchanged"
        result["last_hash"] = last_hash
        _emit(
            args, "INFO",
            f"db 未变化 (hash={db_hash}),跳过备份;用 --force 强制执行",
        )
        # 即使跳过,仍然跑清理(让历史遗留的 backup 收敛)
        if not args.dry_run:
            cleanup_old_backups(
                backup_dir=args.backup_dir,
                keep_daily=args.keep_daily,
                keep_weekly=args.keep_weekly,
                max_size_gb=args.max_size_gb,
            )
        result["finished_at"] = datetime.now().isoformat(timespec="seconds")
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        return 2  # exit 2 = skipped 状态正常

    # ── 准备备份文件路径 ──
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = os.path.join(args.backup_dir, f"db_{timestamp}.sqlite")
    result["backup_path"] = backup_path
    result["last_hash"] = last_hash

    if args.dry_run:
        result["status"] = "dry_run"
        result["would_write"] = backup_path
        result["keep_daily"] = args.keep_daily
        result["keep_weekly"] = args.keep_weekly
        result["max_size_gb"] = args.max_size_gb
        _emit(
            args, "INFO",
            f"[DRY-RUN] 将备份到 {backup_path}, 保留 "
            f"{args.keep_daily}天/{args.keep_weekly}周, 上限 {args.max_size_gb}GB",
        )
        result["finished_at"] = datetime.now().isoformat(timespec="seconds")
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    # ── 实际执行备份 (SQLite backup API,确保 WAL 模式一致性) ──
    import sqlite3
    os.makedirs(args.backup_dir, exist_ok=True)
    src = sqlite3.connect(args.db_path)
    dst = sqlite3.connect(backup_path)
    try:
        src.backup(dst)
    except Exception as e:
        result["status"] = "failed"
        result["reason"] = f"sqlite_backup_error: {e}"
        _emit(args, "ERROR", f"备份失败: {e}")
        if os.path.exists(backup_path):
            os.remove(backup_path)
        result["finished_at"] = datetime.now().isoformat(timespec="seconds")
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1
    finally:
        src.close()
        dst.close()

    size_mb = os.path.getsize(backup_path) / 1024 / 1024
    result["status"] = "ok"
    result["size_mb"] = round(size_mb, 2)
    _emit(
        args, "INFO",
        f"备份完成: {backup_path} ({size_mb:.1f} MB, hash={db_hash})",
    )

    # ── 写元数据 ──
    try:
        with open(meta_path, "w") as f:
            json.dump(
                {
                    "last_hash": db_hash,
                    "last_backup": timestamp,
                    "last_size_mb": round(size_mb, 2),
                },
                f,
            )
    except OSError as e:
        _emit(args, "WARN", f"写 meta 失败(非致命): {e}")

    # ── 清理旧备份 ──
    cleanup_old_backups(
        backup_dir=args.backup_dir,
        keep_daily=args.keep_daily,
        keep_weekly=args.keep_weekly,
        max_size_gb=args.max_size_gb,
    )

    result["finished_at"] = datetime.now().isoformat(timespec="seconds")
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def _emit(args: argparse.Namespace, level: str, msg: str) -> None:
    """统一输出: 文本走 stdout(除非 --json 模式)。"""
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {level}: {msg}"
    if not args.json:
        print(line)


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
