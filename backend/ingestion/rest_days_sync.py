"""
休息天数同步器 — 根据实际赛程计算每支球队的 rest_days

当前所有球队 rest_days=7 (默认值), 此脚本根据已完成比赛的 kickoff_at
重新计算每支球队的最近休息天数。

用法:
    cd backend && python ingestion/rest_days_sync.py
    cd backend && python ingestion/rest_days_sync.py --dry-run
"""
from __future__ import annotations

import os
import sys
import argparse
from datetime import datetime, timezone
from collections import defaultdict
from typing import Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy.orm import Session
from database.models import SessionLocal, Team, Match, MatchStatus
from utils.logger import get_logger

logger = get_logger("rest_days_sync")


def compute_rest_days_batch(db: Session) -> Dict[int, int]:
    """批量计算所有球队的 rest_days，避免 N+1 查询。"""
    from collections import defaultdict

    # 一次性获取所有已完赛比赛
    matches = (
        db.query(Match)
        .filter(Match.status == MatchStatus.FINISHED, Match.kickoff_at.isnot(None))
        .all()
    )

    # 按球队分组收集比赛时间
    team_dates: Dict[int, list] = defaultdict(list)
    for m in matches:
        for kt_col in (m.kickoff_at,):
            if kt_col is None:
                continue
            kt = kt_col
            if isinstance(kt, str):
                try:
                    kt = datetime.fromisoformat(kt.replace("Z", "+00:00"))
                except (ValueError, TypeError):
                    continue
            if kt.tzinfo is None:
                kt = kt.replace(tzinfo=timezone.utc)
            team_dates[m.home_team_id].append(kt)
            team_dates[m.away_team_id].append(kt)

    # 计算每个球队的中位数间隔
    result: Dict[int, int] = {}
    for team_id, dates in team_dates.items():
        if len(dates) < 2:
            continue
        dates.sort()
        gaps = []
        for i in range(1, len(dates)):
            gap = (dates[i] - dates[i - 1]).days
            if 1 <= gap <= 30:
                gaps.append(gap)
        if gaps:
            gaps.sort()
            result[team_id] = gaps[len(gaps) // 2]

    return result


def sync_rest_days(db: Session, dry_run: bool = False) -> Dict:
    """为所有球队计算并更新 rest_days（批量优化版）。"""
    teams = db.query(Team).all()
    batch = compute_rest_days_batch(db)

    stats = {"total": len(teams), "updated": 0, "unchanged": 0, "no_data": 0, "errors": 0}

    for team in teams:
        try:
            rest = batch.get(team.id)
            if rest is None:
                stats["no_data"] += 1
                continue

            old_val = team.rest_days or 7
            if old_val != rest and old_val == 7:
                if not dry_run:
                    team.rest_days = rest
                stats["updated"] += 1
                logger.info(f"[rest_days] {team.name}: {old_val} -> {rest}")
            else:
                stats["unchanged"] += 1

        except Exception as e:
            logger.error(f"[rest_days] Error for {team.name}: {e}")
            stats["errors"] += 1

    if not dry_run:
        db.commit()

    return stats


def main():
    parser = argparse.ArgumentParser(description="休息天数同步器")
    parser.add_argument("--dry-run", action="store_true", help="仅输出不写入")
    args = parser.parse_args()

    print("=" * 60)
    print("  休息天数同步 (Rest Days Sync)")
    print("=" * 60)

    db = SessionLocal()
    try:
        t0 = __import__('time').time()
        stats = sync_rest_days(db, dry_run=args.dry_run)
        elapsed = __import__('time').time() - t0

        print(f"\n同步完成 ({elapsed:.1f}s):")
        print(f"  总球队数: {stats['total']}")
        print(f"  已更新: {stats['updated']}")
        print(f"  无需更改: {stats['unchanged']}")
        print(f"  无数据: {stats['no_data']}")
        print(f"  错误: {stats['errors']}")

        # 分布统计
        dist: Dict[int, int] = defaultdict(int)
        for team in db.query(Team).all():
            dist[team.rest_days or 7] += 1
        print("\n  rest_days 分布:")
        for days in sorted(dist.keys()):
            print(f"    {days:>3} 天: {dist[days]} 队")

    finally:
        db.close()


if __name__ == "__main__":
    main()
