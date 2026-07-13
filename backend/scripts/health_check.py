"""
健康检查 CLI 工具
==================

脱离 scheduler 单独运行 health_daemon,刷新 backend/data/health_status.json。

修复: 6-16 动态审计发现 health_status.json 时间戳停在 5-24,
      scheduler 上的 health_daemon 任务可能没在跑或被异常吞掉。
      提供 CLI 入口,运维人员可手动触发 + CI 可监控。

用法:
    # 默认(写入 backend/data/health_status.json)
    python -m scripts.health_check

    # 写到指定路径
    python -m scripts.health_check --output /tmp/health.json

    # JSON 输出(CI 集成)
    python -m scripts.health_check --json

    # 只跑不写,看临时结果
    python -m scripts.health_check --dry-run

    # 警告时不退出非零(默认 critical 才 exit 1)
    python -m scripts.health_check --strict

设计要点:
    - 不重写 health 逻辑,直接调用 HealthDaemon.run_all_checks()
    - exit code: 0=ok/warn, 1=critical, 2=exception
    - JSON 模式只输出结构化,不写文件
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime

# ── 路径注入: 让 `python -m scripts.health_check` 找到 backend 根 ──
_CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
_BACKEND_ROOT = os.path.dirname(_CURRENT_DIR)
if _BACKEND_ROOT not in sys.path:
# health_daemon 用的是同级模块导入(alert_manager, monitor.xxx)
# 加上 backend 根的子目录,让 `from monitor.alert_manager import ...` 也能工作
_MONITOR_DIR = os.path.join(_BACKEND_ROOT, "monitor")
if _MONITOR_DIR not in sys.path:


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="WC Analytics 健康检查 CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  %(prog)s                       # 默认配置
  %(prog)s --json                # 输出 JSON
  %(prog)s --dry-run             # 不写文件
  %(prog)s --strict              # warn 也算失败
  %(prog)s --output /tmp/h.json  # 写到指定路径
        """,
    )
    p.add_argument(
        "--output",
        default=os.path.join(_BACKEND_ROOT, "data", "health_status.json"),
        help="输出文件路径(默认: backend/data/health_status.json)",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="不写文件,只在 stdout 打印结果",
    )
    p.add_argument(
        "--strict",
        action="store_true",
        help="严格模式: warn 也 exit 1(默认只 critical exit 1)",
    )
    p.add_argument(
        "--json",
        action="store_true",
        help="输出 JSON 格式(便于 CI 集成)",
    )
    return p.parse_args()


def main() -> int:
    args = parse_args()
    started = datetime.utcnow().isoformat()

    # ── 延迟导入(避免 --help 时加载重模块) ──
    try:
        from monitor.health_daemon import HealthDaemon
    except Exception as e:
        print(f"FATAL: 加载 HealthDaemon 失败: {e}", file=sys.stderr)
        if args.json:
            print(json.dumps({
                "status": "exception",
                "reason": f"import_error: {e}",
                "started_at": started,
            }, ensure_ascii=False, indent=2))
        return 2

    # ── 跑健康检查 ──
    try:
        daemon = HealthDaemon()
        report = daemon.run_all_checks()
    except Exception as e:
        print(f"FATAL: 健康检查执行失败: {e}", file=sys.stderr)
        if args.json:
            print(json.dumps({
                "status": "exception",
                "reason": f"exec_error: {e}",
                "started_at": started,
            }, ensure_ascii=False, indent=2))
        return 2

    result = report.to_dict()

    # ── 决定 exit code ──
    overall = result.get("overall", "unknown").lower()
    if overall == "critical":
        exit_code = 1
    elif overall == "warning" and args.strict:
        exit_code = 1
    else:
        exit_code = 0

    # ── 写文件(非 dry-run) ──
    written_path = None
    if not args.dry_run:
        try:
            os.makedirs(os.path.dirname(args.output), exist_ok=True)
            with open(args.output, "w") as f:
                json.dump(result, f, indent=2, ensure_ascii=False)
            written_path = args.output
        except OSError as e:
            print(f"ERROR: 写文件失败: {e}", file=sys.stderr)
            if args.json:
                result["_write_error"] = str(e)
            else:
                return 1

    # ── 输出 ──
    finished = datetime.utcnow().isoformat()
    if args.json:
        print(json.dumps({
            "status": "ok",
            "overall": overall,
            "exit_code": exit_code,
            "written_to": written_path,
            "started_at": started,
            "finished_at": finished,
            "report": result,
        }, ensure_ascii=False, indent=2))
    else:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] 状态: {overall}")
        print(f"[{datetime.now().strftime('%H:%M:%S')}] 检查数: {len(result.get('checks', []))}")
        for c in result.get("checks", []):
            name = c.get("name", "?")
            status = c.get("status", "?")
            msg = c.get("message", "")
            print(f"  - {name:30s} {status:8s}  {msg}")
        if written_path:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] 写入: {written_path}")
        elif args.dry_run:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] dry-run: 未写文件")

    return exit_code


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
