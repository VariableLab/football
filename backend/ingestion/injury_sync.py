"""
球员伤停数据同步器

支持两个数据源:
1. API-Football (api-football.com) — 付费, 100 req/day 免费, 覆盖 100+ 联赛
2. football-data.org — 免费, 10 calls/min, 无专用伤停端点(仅作补充)

数据流:
- API-Football /injuries → 解析 → 更新 teams.key_injuries 字段
- 每日定时同步, 在比赛开始前更新

用 法:
  python3 injury_sync.py              # 同步未来7天比赛的伤停
  python3 injury_sync.py --all        # 同步所有有赔率比赛的伤停
  python3 injury_sync.py --team 123   # 只同步指定球队
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

import requests
from sqlalchemy.orm import Session

from database.models import SessionLocal, Match, MatchStatus, Team
from utils.logger import get_logger

logger = get_logger("injury_sync")

# API-Football 基础 URL
API_FOOTBALL_BASE = "https://v3.football.api-sports.io"


class InjurySync:
    """球员伤停数据同步"""

    def __init__(self, db: Session, api_key: Optional[str] = None):
        self.db = db
        self.api_key = api_key or os.environ.get("API_FOOTBALL_KEY", "")
        self._team_name_map: Dict[str, int] = {}
        self._build_team_name_map()

    def _build_team_name_map(self) -> None:
        """构建球队名称→ID映射, 支持模糊匹配"""
        teams = self.db.query(Team).all()
        for team in teams:
            if team.name:
                self._team_name_map[team.name.lower()] = team.id
                # 也存缩写/别名
                if team.short_name:
                    self._team_name_map[team.short_name.lower()] = team.id

    def _find_team_id(self, name: str) -> Optional[int]:
        """通过名称查找本地球队ID"""
        lower = name.lower().strip()
        # 精确匹配
        if lower in self._team_name_map:
            return self._team_name_map[lower]
        # 包含匹配 (API 名称可能略不同)
        for key, tid in self._team_name_map.items():
            if lower in key or key in lower:
                return tid
        return None

    def _api_football_request(self, endpoint: str, params: Dict) -> Optional[List]:
        """调用 API-Football 接口"""
        if not self.api_key:
            logger.warning("[injury-sync] No API-Football key configured")
            return None

        headers = {"x-apisports-key": self.api_key}
        url = f"{API_FOOTBALL_BASE}/{endpoint}"

        try:
            resp = requests.get(url, headers=headers, params=params, timeout=15)
            resp.raise_for_status()
            data = resp.json()

            if data.get("errors"):
                logger.error(f"[injury-sync] API errors: {data['errors']}")
                return None

            results = data.get("response", [])
            remaining = data.get("paging", {}).get("total", 0)
            logger.info(
                f"[injury-sync] API-Football {endpoint}: {len(results)} results"
            )
            return results

        except requests.RequestException as e:
            logger.error(f"[injury-sync] API request failed: {e}")
            return None

    def fetch_injuries(self, fixture_id: int) -> List[Dict]:
        """获取指定比赛的伤停信息"""
        results = self._api_football_request("injuries", {"fixture": fixture_id})
        if not results:
            return []

        injuries = []
        for item in results:
            player = item.get("player", {})
            team = item.get("team", {})
            reason = item.get("reason", "Unknown")

            injuries.append({
                "player_name": player.get("name", "Unknown"),
                "team_name": team.get("name", ""),
                "reason": reason,
                "type": "injury" if "Injury" in reason else "suspension",
            })

        return injuries

    def fetch_injuries_by_date(self, date: str) -> List[Dict]:
        """获取指定日期所有比赛的伤停 (date format: YYYY-MM-DD)"""
        results = self._api_football_request("injuries", {"date": date})
        if not results:
            return []

        injuries = []
        for item in results:
            player = item.get("player", {})
            team = item.get("team", {})
            reason = item.get("reason", "Unknown")

            injuries.append({
                "player_name": player.get("name", "Unknown"),
                "team_name": team.get("name", ""),
                "reason": reason,
                "type": "injury" if "Injury" in reason else "suspension",
            })

        return injuries

    def format_key_injuries(self, injuries: List[Dict], team_name: str) -> str:
        """格式化伤停列表为 key_injuries 字段值"""
        team_injuries = [i for i in injuries if i["team_name"] == team_name]
        if not team_injuries:
            # 也尝试模糊匹配
            for inj in injuries:
                local_id = self._find_team_id(inj["team_name"])
                if local_id:
                    team_injuries_by_id = [
                        i for i in injuries
                        if self._find_team_id(i["team_name"]) == local_id
                    ]
                    team_injuries = team_injuries_by_id
                    break

        # 只取核心伤停(最多5人)
        parts = []
        for inj in team_injuries[:5]:
            tag = "伤" if inj["type"] == "injury" else "停"
            name = inj["player_name"]
            # 截断过长名字
            if len(name) > 20:
                name = name[:18] + ".."
            parts.append(f"{name}({tag})")

        return ",".join(parts)

    def sync_upcoming(self, days: int = 7) -> int:
        """同步未来N天比赛的伤停数据"""
        updated = 0
        now = datetime.now(timezone.utc)
        future_end = now + timedelta(days=days)

        matches = self.db.query(Match).filter(
            Match.status.in_([MatchStatus.SCHEDULED, MatchStatus.UPCOMING]),
            Match.kickoff_at.isnot(None),
            Match.kickoff_at <= future_end,
        ).all()

        logger.info(f"[injury-sync] Found {len(matches)} upcoming matches")

        # 按日期聚合请求, 减少API调用
        dates = set()
        for m in matches:
            if m.kickoff_at:
                if isinstance(m.kickoff_at, str):
                    dt = datetime.fromisoformat(m.kickoff_at.replace("Z", "+00:00"))
                else:
                    dt = m.kickoff_at
                dates.add(dt.strftime("%Y-%m-%d"))

        # 获取每天的伤停数据
        all_injuries = []
        for date_str in sorted(dates):
            injuries = self.fetch_injuries_by_date(date_str)
            if injuries:
                all_injuries.extend(injuries)

        # 更新球队伤停字段
        if all_injuries:
            teams_updated = set()
            for injury in all_injuries:
                team_name = injury["team_name"]
                team_id = self._find_team_id(team_name)
                if team_id and team_id not in teams_updated:
                    key_injuries = self.format_key_injuries(all_injuries, team_name)
                    if key_injuries:
                        team = self.db.query(Team).get(team_id)
                        if team:
                            team.key_injuries = key_injuries
                            teams_updated.add(team_id)
                            updated += 1

            self.db.commit()

        logger.info(f"[injury-sync] Updated {updated} teams with injury data")
        return updated

    def sync_manual(self, team_id: int, injuries: str) -> bool:
        """手动设置球队伤停 (injuries 格式: "梅西(伤),内马尔(停)")"""
        team = self.db.query(Team).get(team_id)
        if not team:
            logger.error(f"[injury-sync] Team {team_id} not found")
            return False

        team.key_injuries = injuries
        self.db.commit()
        logger.info(f"[injury-sync] Manually set injuries for team {team_id}: {injuries}")
        return True

    def clear_stale_injuries(self, days: int = 3) -> int:
        """清除超过N天的旧伤停数据"""
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)

        # 查找已结束比赛的球队, 清除伤停
        finished_match_teams = self.db.query(Match).filter(
            Match.status == MatchStatus.FINISHED,
            Match.kickoff_at < cutoff,
        ).all()

        team_ids = set()
        for m in finished_match_teams:
            team_ids.add(m.home_team_id)
            team_ids.add(m.away_team_id)

        cleared = 0
        for tid in team_ids:
            team = self.db.query(Team).get(tid)
            if team and team.key_injuries:
                team.key_injuries = ""
                cleared += 1

        self.db.commit()
        logger.info(f"[injury-sync] Cleared stale injuries for {cleared} teams")
        return cleared


# ────────────────────────────
# CLI
# ────────────────────────────
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Injury data sync")
    parser.add_argument("--all", action="store_true", help="Sync all matches")
    parser.add_argument("--team", type=int, help="Sync specific team ID")
    parser.add_argument("--set", type=str, help="Manual set injuries (use with --team)")
    parser.add_argument("--days", type=int, default=7, help="Days ahead to sync")
    parser.add_argument("--clear-stale", action="store_true", help="Clear old injuries")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        sync = InjurySync(db)

        if args.set and args.team:
            ok = sync.sync_manual(args.team, args.set)
            print(f"Manual set: {'OK' if ok else 'FAILED'}")
        elif args.clear_stale:
            count = sync.clear_stale_injuries()
            print(f"Cleared {count} teams")
        else:
            count = sync.sync_upcoming(days=args.days)
            print(f"Updated {count} teams with injury data")
    finally:
        db.close()
