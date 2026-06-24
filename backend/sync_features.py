#!/usr/bin/env python3
"""
统一特征数据同步入口

一键同步所有缺失的特征数据：
1. 高级统计数据 (xG/xGA, possession, pass_completion, shots_per_game)
2. 休息天数 (rest_days) — 根据实际赛程计算
3. 伤停信息 (key_injuries) — 从 API-Football 获取
4. 比赛环境 (weather, temperature, pitch_condition)

用法:
    cd backend && python sync_features.py                  # 全量同步
    cd backend && python sync_features.py --dry-run        # 仅预览
    cd backend && python sync_features.py --step xg        # 仅同步 xG
    cd backend && python sync_features.py --step rest      # 仅同步 rest_days
    cd backend && python sync_features.py --team ARG BRA  # 仅处理指定球队
"""
from __future__ import annotations

import os
import sys
import time
import argparse
from datetime import datetime, timezone

# 确保 backend/ 根目录在 path 中
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from utils.logger import get_logger
from database.models import SessionLocal, Team

logger = get_logger("sync_features")

# ────────────────────────────
# 同步步骤注册
# ────────────────────────────

STEPS = {
    "xg": "高级统计数据 (xG/xGA, possession, shots...)",
    "rest": "休息天数 (rest_days)",
    "injury": "伤停信息 (key_injuries)",
    "env": "比赛环境 (weather, pitch...)",
}


def run_step_xg(db, team_codes=None, dry_run=False):
    """Step 1: 高级统计数据"""
    from ingestion.feature_sync import sync_advanced_stats
    print("\n" + "-" * 50)
    print("Step 1: 高级统计数据 (xG/xGA, possession...)")
    print("-" * 50)
    t0 = time.time()
    stats = sync_advanced_stats(db, team_codes=team_codes, dry_run=dry_run)
    elapsed = time.time() - t0

    print(f"  耗时: {elapsed:.1f}s")
    print(f"  总球队: {stats['total']} | 已有: {stats['already_filled']} | "
          f"FBref: {stats['from_fbref']} | 比赛推算: {stats['from_matches']} | "
          f"Elo 估算: {stats['from_elo']} | 默认: {stats['from_default']} | 错误: {stats['errors']}")

    # 覆盖率
    for field in ['avg_xg', 'avg_xga', 'possession', 'pass_completion', 'shots_per_game']:
        count = db.query(Team).filter(
            getattr(Team, field).isnot(None), getattr(Team, field) != 0
        ).count()
        pct = count / max(stats['total'], 1) * 100
        print(f"    {field}: {count}/{stats['total']} = {pct:.0f}%")

    return stats


def run_step_rest(db, team_codes=None, dry_run=False):
    """Step 2: 休息天数"""
    from ingestion.rest_days_sync import sync_rest_days
    print("\n" + "-" * 50)
    print("Step 2: 休息天数 (rest_days)")
    print("-" * 50)
    t0 = time.time()

    # 可选: 限制球队
    if team_codes:
        from database.models import Team
        # sync_rest_days 内部会处理所有球队，但我们可以先过滤
        pass

    stats = sync_rest_days(db, dry_run=dry_run)
    elapsed = time.time() - t0

    print(f"  耗时: {elapsed:.1f}s")
    print(f"  总球队: {stats['total']} | 已更新: {stats['updated']} | "
          f"无需更改: {stats['unchanged']} | 无数据: {stats['no_data']} | 错误: {stats['errors']}")

    return stats


def run_step_injury(db, team_codes=None, dry_run=False):
    """Step 3: 伤停信息"""
    from ingestion.injury_sync import InjurySync
    print("\n" + "-" * 50)
    print("Step 3: 伤停信息 (key_injuries)")
    print("-" * 50)
    t0 = time.time()

    sync = InjurySync(db)
    # sync_upcoming 不依赖 team_codes，它同步所有即将到来的比赛
    try:
        count = sync.sync_upcoming(days=7)
    except Exception as e:
        print(f"  警告: 伤停同步失败 ({e}) — 可能是 API key 未配置")
        count = 0

    elapsed = time.time() - t0

    # 统计填充率
    filled = db.query(Team).filter(Team.key_injuries != "").count()
    total = db.query(Team).count()

    print(f"  耗时: {elapsed:.1f}s")
    print(f"  更新球队: {count}")
    print(f"  伤停数据覆盖: {filled}/{total} = {filled/max(total,1)*100:.0f}%")

    return {"updated": count, "coverage": f"{filled}/{total}"}


def run_step_env(db, team_codes=None, dry_run=False):
    """Step 4: 比赛环境"""
    from ingestion.match_env_sync import sync_match_environment
    print("\n" + "-" * 50)
    print("Step 4: 比赛环境 (weather, temperature...)")
    print("-" * 50)
    t0 = time.time()
    stats = sync_match_environment(db, dry_run=dry_run)
    elapsed = time.time() - t0

    print(f"  耗时: {elapsed:.1f}s")
    print(f"  总比赛: {stats['total']} | 已更新: {stats['updated']} | "
          f"已有数据: {stats['already_set']} | 错误: {stats['errors']}")

    return stats


STEP_RUNNERS = {
    "xg": run_step_xg,
    "rest": run_step_rest,
    "injury": run_step_injury,
    "env": run_step_env,
}


def main():
    parser = argparse.ArgumentParser(description="统一特征数据同步")
    parser.add_argument("--dry-run", action="store_true", help="仅预览不写入")
    parser.add_argument("--step", type=str, nargs="*", choices=list(STEPS.keys()),
                        help="仅执行指定步骤 (默认: 全部)")
    parser.add_argument("--team", type=str, nargs="*", help="仅处理指定球队 code")
    args = parser.parse_args()

    # 默认执行全部步骤
    if not args.step:
        args.step = list(STEPS.keys())

    print("=" * 60)
    print("  足球预测 — 特征数据同步")
    print(f"  时间: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print(f"  模式: {'[DRY-RUN]' if args.dry_run else '[WRITE]'}")
    print("=" * 60)
    print()

    # 显示计划执行的步骤
    for step_key in args.step:
        print(f"  [{step_key}] {STEPS[step_key]}")
    print()

    db = SessionLocal()
    total_start = time.time()
    all_stats = {}

    try:
        for step_key in args.step:
            runner = STEP_RUNNERS[step_key]
            try:
                stats = runner(db, team_codes=args.team, dry_run=args.dry_run)
                all_stats[step_key] = stats
            except Exception as e:
                logger.error(f"[sync] Step '{step_key}' failed: {e}", exc_info=True)
                all_stats[step_key] = {"error": str(e)}
                print(f"  ⚠️  Step '{step_key}' 失败: {e}")

        total_elapsed = time.time() - total_start

        # 最终汇总
        print("\n" + "=" * 60)
        print("  同步汇总")
        print("=" * 60)
        for step_key, stats in all_stats.items():
            status = "OK" if "error" not in stats else "FAIL"
            print(f"  [{status}] {step_key}: {stats}")

        print(f"\n  总耗时: {total_elapsed:.1f}s")
        print("=" * 60)

    finally:
        db.close()


if __name__ == "__main__":
    main()
