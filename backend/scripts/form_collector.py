#!/usr/bin/env python3
"""
近期状态采集器 (Form Collector)

功能：
  1. 从数据库已有比赛推导各队近期战绩
  2. 可选接入 football-data.org API 补充外部数据
  3. 计算并更新 teams 表的 recent_results / recent_goals_* / form_factor

用法：
    cd backend
    source venv/bin/activate
    python form_collector.py

环境变量（可选）：
    FOOTBALL_DATA_API_KEY — football-data.org API Key（免费 tier 100 calls/day）
"""

from __future__ import annotations

import os
import sys
from datetime import datetime
from typing import Dict, List, Optional

import httpx
from sqlalchemy.orm import Session

from database.config import get_settings
from database.models import SessionLocal, Team, Match, MatchStatus
from utils.logger import get_logger

logger = get_logger("form")
settings = get_settings()

# ────────────────────────────
# 常量
# ────────────────────────────
FORM_WINDOW = 10               # 近期战绩取近 N 场
MIN_MATCHES_FOR_FACTOR = 3     # 至少 N 场才计算 form_factor

# football-data.org 球队 ID 映射（常用国家队）
# 来源：https://api.football-data.org/v4/teams?areas=2077,2267,2224,2233,2001,2015,2011
FD_TEAM_IDS: Dict[str, int] = {
    "ARG": 762, "AUS": 779, "BEL": 805, "BRA": 764, "CAN": 828,
    "CMR": 802, "CRC": 793, "CRO": 799, "DEN": 770, "ECU": 791,
    "ENG": 770, "FRA": 773, "GER": 759, "GHA": 788, "IRN": 781,
    "JPN": 766, "KOR": 772, "KSA": 801, "MEX": 769, "MAR": 802,
    "NED": 860, "POL": 794, "POR": 765, "QAT": 803, "SEN": 789,
    "SRB": 780, "ESP": 760, "SUI": 788, "TUN": 802, "URU": 758,
    "USA": 766, "WAL": 833,
}


# ────────────────────────────
# Source 1: 内部数据库推导
# ────────────────────────────
class InternalFormSource:
    """从 database.sqlite 的 matches 表推导近期战绩"""

    @classmethod
    def compute_for_team(cls, db: Session, team_id: int) -> Optional[Dict]:
        """为指定球队计算近期战绩"""
        team = db.query(Team).filter(Team.id == team_id).first()
        if not team:
            return None

        # 查询该球队所有已结束的比赛（按时间倒序）
        home_matches = (
            db.query(Match)
            .filter(
                Match.home_team_id == team_id,
                Match.status == MatchStatus.FINISHED,
                Match.actual_home_goals.isnot(None),
            )
            .order_by(Match.kickoff_at.desc())
            .limit(FORM_WINDOW)
            .all()
        )
        away_matches = (
            db.query(Match)
            .filter(
                Match.away_team_id == team_id,
                Match.status == MatchStatus.FINISHED,
                Match.actual_away_goals.isnot(None),
            )
            .order_by(Match.kickoff_at.desc())
            .limit(FORM_WINDOW)
            .all()
        )

        # 合并并按时间排序（最近的在前）
        all_matches = []
        for m in home_matches:
            all_matches.append({
                "date": m.kickoff_at,
                "is_home": True,
                "gf": m.actual_home_goals,
                "ga": m.actual_away_goals,
                "opp": m.away_team.name if m.away_team else "?",
            })
        for m in away_matches:
            all_matches.append({
                "date": m.kickoff_at,
                "is_home": False,
                "gf": m.actual_away_goals,
                "ga": m.actual_home_goals,
                "opp": m.home_team.name if m.home_team else "?",
            })

        all_matches.sort(key=lambda x: x["date"] or datetime.min, reverse=True)
        recent = all_matches[:FORM_WINDOW]

        if len(recent) < MIN_MATCHES_FOR_FACTOR:
            return None

        # 计算 W/D/L 序列和场均进球
        results_str = ""
        total_gf = 0
        total_ga = 0
        for m in recent:
            gf, ga = m["gf"], m["ga"]
            total_gf += gf
            total_ga += ga
            if gf > ga:
                results_str += "W"
            elif gf == ga:
                results_str += "D"
            else:
                results_str += "L"

        n = len(recent)
        avg_gf = round(total_gf / n, 2)
        avg_ga = round(total_ga / n, 2)

        # 计算 form_factor（与 prediction_engine.py 中 FormAdjustmentModel 保持一致）
        form_factor = cls._compute_form_factor(results_str)

        return {
            "recent_results": results_str,
            "recent_goals_scored": avg_gf,
            "recent_goals_conceded": avg_ga,
            "form_factor": round(form_factor, 3),
            "matches_used": n,
            "source": "internal",
        }

    @staticmethod
    def _compute_form_factor(results: str) -> float:
        """
        根据 WDL 字符串计算 form_factor。
        与 prediction_engine.FormAdjustmentModel 算法一致。
        """
        if not results:
            return 1.0

        results = results[-10:]  # 取最近 10 场
        n = len(results)
        if n == 0:
            return 1.0

        # 权重：最近一场 = 1.0，最早一场 = 0.5
        weights = [0.5 + 0.5 * (i / max(n - 1, 1)) for i in range(n)]
        points_map = {"W": 3, "D": 1, "L": 0}
        points = [points_map.get(r.upper(), 1) for r in results]

        weighted_avg = sum(p * w for p, w in zip(points, weights)) / sum(weights)
        # 1.5 分 = 中性(1.0), 3 分 = +10%, 0 分 = -15%
        return max(0.75, min(1.15, 0.85 + 0.10 * (weighted_avg / 1.5)))


# ────────────────────────────
# Source 2: football-data.org API
# ────────────────────────────
class FootballDataSource:
    """从 football-data.org 抓取近期战绩"""

    BASE_URL = "https://api.football-data.org/v4"

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.getenv("FOOTBALL_DATA_API_KEY")
        self.client = httpx.Client(timeout=15.0, headers={
            "X-Auth-Token": self.api_key or "",
        })

    def is_available(self) -> bool:
        return bool(self.api_key)

    def fetch_team_matches(self, fd_team_id: int, limit: int = 10) -> List[Dict]:
        """获取指定球队的近期已结束比赛"""
        if not self.is_available():
            return []

        url = f"{self.BASE_URL}/teams/{fd_team_id}/matches"
        params = {"status": "FINISHED", "limit": limit}

        try:
            resp = self.client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()
            return data.get("matches", [])
        except Exception as e:
            logger.warning(f"football-data API 请求失败: {e}")
            return []

    def compute_for_team(self, team_code: str) -> Optional[Dict]:
        fd_id = FD_TEAM_IDS.get(team_code.upper())
        if not fd_id:
            logger.debug(f"未找到 {team_code} 的 football-data ID 映射")
            return None

        matches = self.fetch_team_matches(fd_id, limit=FORM_WINDOW)
        if not matches:
            return None

        results_str = ""
        total_gf = 0
        total_ga = 0

        for m in matches:
            home = m.get("homeTeam", {})
            away = m.get("awayTeam", {})
            score = m.get("score", {}).get("fullTime", {})
            home_goals = score.get("home", 0) or 0
            away_goals = score.get("away", 0) or 0

            # 判断该球队是主队还是客队
            is_home = home.get("tla") == team_code.upper()
            if is_home:
                gf, ga = home_goals, away_goals
            else:
                gf, ga = away_goals, home_goals

            total_gf += gf
            total_ga += ga
            if gf > ga:
                results_str += "W"
            elif gf == ga:
                results_str += "D"
            else:
                results_str += "L"

        n = len(matches)
        avg_gf = round(total_gf / n, 2)
        avg_ga = round(total_ga / n, 2)
        form_factor = InternalFormSource._compute_form_factor(results_str)

        return {
            "recent_results": results_str,
            "recent_goals_scored": avg_gf,
            "recent_goals_conceded": avg_ga,
            "form_factor": round(form_factor, 3),
            "matches_used": n,
            "source": "football-data.org",
        }


# ────────────────────────────
# 主控逻辑
# ────────────────────────────
class FormCollector:
    """近期状态采集主控"""

    def __init__(self, db: Session):
        self.db = db
        self.internal = InternalFormSource()
        self.external = FootballDataSource()

    def refresh_team(self, team: Team, use_external: bool = True) -> bool:
        """
        刷新单支球队的近期状态。
        优先使用内部数据，如不足且 external 可用则补充。
        """
        # 1. 先尝试内部数据
        data = self.internal.compute_for_team(self.db, team.id)

        # 2. 内部不足且外部可用时，尝试外部
        if (data is None or data["matches_used"] < FORM_WINDOW) and use_external and self.external.is_available():
            ext_data = self.external.compute_for_team(team.code)
            if ext_data and ext_data["matches_used"] >= (data["matches_used"] if data else 0):
                data = ext_data
                logger.info(f"  [{team.code}] 使用外部数据: {ext_data['recent_results']}")

        if not data:
            logger.debug(f"  [{team.code}] 无可用近期战绩数据")
            return False

        # 3. 更新数据库
        team.recent_results = data["recent_results"]
        team.recent_goals_scored = data["recent_goals_scored"]
        team.recent_goals_conceded = data["recent_goals_conceded"]
        team.form_factor = data["form_factor"]

        self.db.commit()
        logger.info(
            f"  [{team.code}] {team.name:<12} | "
            f"近{data['matches_used']}场: {data['recent_results']} | "
            f"均进{data['recent_goals_scored']:.1f} 失{data['recent_goals_conceded']:.1f} | "
            f"状态{data['form_factor']:.2f} | 来源:{data['source']}"
        )
        return True

    def refresh_all(self, use_external: bool = True) -> Dict[str, int]:
        """刷新所有球队的近期状态"""
        teams = self.db.query(Team).all()
        logger.info(f"🔄 开始刷新 {len(teams)} 支球队的近期状态...")

        updated = 0
        skipped = 0
        failed = 0

        for team in teams:
            try:
                if self.refresh_team(team, use_external=use_external):
                    updated += 1
                else:
                    skipped += 1
            except Exception as e:
                logger.error(f"  [{team.code}] 刷新失败: {e}")
                failed += 1

        logger.info(
            f"✅ 完成: 更新 {updated} 支, 跳过 {skipped} 支, 失败 {failed} 支"
        )
        return {"updated": updated, "skipped": skipped, "failed": failed}


# ────────────────────────────
# CLI 入口
# ────────────────────────────
def main():
    import argparse
    parser = argparse.ArgumentParser(description="近期状态采集器")
    parser.add_argument("--team", "-t", help="指定球队 code（如 ARG），不指定则全部")
    parser.add_argument("--no-external", action="store_true", help="禁用外部 API")
    parser.add_argument("--dry-run", action="store_true", help="只打印，不写入数据库")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        collector = FormCollector(db)

        if args.team:
            team = db.query(Team).filter(Team.code == args.team.upper()).first()
            if not team:
                print(f"❌ 未找到球队: {args.team}")
                sys.exit(1)

            data = collector.internal.compute_for_team(db, team.id)
            if not data and not args.no_external and collector.external.is_available():
                data = collector.external.compute_for_team(team.code)

            if data:
                print(f"\n📊 [{team.code}] {team.name}")
                print(f"   近期战绩: {data['recent_results']}")
                print(f"   场均进球: {data['recent_goals_scored']}")
                print(f"   场均失球: {data['recent_goals_conceded']}")
                print(f"   状态因子: {data['form_factor']}")
                print(f"   数据来源: {data['source']}")

                if not args.dry_run:
                    collector.refresh_team(team, use_external=not args.no_external)
                    print("   ✅ 已写入数据库")
            else:
                print(f"⚠️ [{team.code}] 无可用数据")
        else:
            stats = collector.refresh_all(use_external=not args.no_external)
            print(f"\n📈 统计: 更新 {stats['updated']} | 跳过 {stats['skipped']} | 失败 {stats['failed']}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
