"""
SoccerData (github.com/probberechts/soccerdata) 集成适配器

功能：
  1. 通过 SoccerData 库抓取 8 大数据源的足球数据
  2. 将 DataFrame 结果转为本项目内部数据结构
  3. 与 odds_collector / form_collector / prediction_engine 协作

支持的数据源：
  - Club Elo        → 球队 Elo 等级分历史
  - ESPN            → 赛事赛程与比分
  - FBref           → 详细球员/球队统计（xG、传球、射门等）
  - Football-Data   → 历史比赛 + 多博彩公司赔率
  - Sofascore       → 实时评分与事件
  - SoFIFA          → FIFA 游戏球员能力值
  - Understat       → xG 数据（射门级别）
  - WhoScored       → 球员评分与战术统计

安装：
    pip install soccerdata>=1.9.0

许可证：MIT（与 SoccerData 一致）
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any

import pandas as pd
from sqlalchemy.orm import Session

from logger import get_logger

logger = get_logger("soccerdata")

# ─── 常量 ──────────────────────────────────────────
CACHE_DIR = Path(__file__).parent.parent / ".soccerdata_cache"
CACHE_DIR.mkdir(exist_ok=True)

# SoccerData 支持的联赛标识符（部分常用）
LEAGUE_MAP = {
    # 国家队赛事（SoccerData 内部标识符）
    "world_cup": "INT-World Cup",
    "euro": "INT-European Championship",
    # 五大联赛（俱乐部，备用）
    "epl": "ENG-Premier League",
    "laliga": "ESP-La Liga",
    "bundesliga": "GER-Bundesliga",
    "serie_a": "ITA-Serie A",
    "ligue_1": "FRA-Ligue 1",
}

# 数据源名称映射
SOURCE_NAMES = {
    "elo": "ClubElo",
    "fbref": "FBref",
    "footballdata": "FootballData",
    "understat": "Understat",
    "whoscored": "WhoScored",
    "sofascore": "Sofascore",
    "sofifa": "SoFIFA",
    "espn": "ESPN",
}


# ─── 数据结构 ──────────────────────────────────────
@dataclass
class TeamStats:
    """球队赛季统计数据"""
    team_name: str
    season: str
    matches_played: int = 0
    goals_for: float = 0.0
    goals_against: float = 0.0
    xg_for: float = 0.0
    xg_against: float = 0.0
    shots_per_game: float = 0.0
    possession: float = 0.0
    pass_completion: float = 0.0
    source: str = ""


@dataclass
class PlayerStats:
    """球员赛季统计数据"""
    player_name: str
    team_name: str
    season: str
    minutes: int = 0
    goals: int = 0
    assists: int = 0
    xg: float = 0.0
    xa: float = 0.0          # expected assists
    shots: int = 0
    key_passes: int = 0
    yellow_cards: int = 0
    red_cards: int = 0
    source: str = ""


@dataclass
class EloRating:
    """Elo 等级分记录"""
    team_name: str
    rating: float
    rank: Optional[int] = None
    date: Optional[datetime] = None
    source: str = "clubelo"


@dataclass
class HistoricalMatch:
    """历史比赛记录（含赔率）"""
    date: str
    home_team: str
    away_team: str
    home_goals: Optional[int]
    away_goals: Optional[int]
    odds_home: Optional[float] = None
    odds_draw: Optional[float] = None
    odds_away: Optional[float] = None
    # 多博彩公司赔率
    odds_map: Dict[str, Dict[str, float]] = field(default_factory=dict)
    source: str = "football-data"


# ─── SoccerData 客户端封装 ─────────────────────────
class SoccerDataClient:
    """
    SoccerData 库的轻量级封装。
    按需初始化各数据源的 Scraper，带错误隔离。
    """

    def __init__(self, cache_path: Optional[Path] = None):
        self.cache_path = str(cache_path or CACHE_DIR)
        self._scrapers: Dict[str, Any] = {}

    def _get_scraper(self, name: str, *args, **kwargs):
        """懒加载并缓存 scraper 实例"""
        import soccerdata as sd

        key = f"{name}:{json.dumps({'args': args, 'kwargs': kwargs}, sort_keys=True, default=str)}"
        if key not in self._scrapers:
            cls = getattr(sd, name)
            # 传入统一的缓存目录
            self._scrapers[key] = cls(*args, **kwargs)
        return self._scrapers[key]

    # ─── Club Elo ─────────────────────────────────
    def fetch_elo_ratings(self, date_str: Optional[str] = None) -> List[EloRating]:
        """
        获取指定日期的 Club Elo 等级分。
        date_str: YYYY-MM-DD，默认今天。
        """
        try:
            import soccerdata as sd
        except ImportError:
            logger.warning("[soccerdata] soccerdata not installed. Run: pip install soccerdata")
            return []

        date_str = date_str or datetime.utcnow().strftime("%Y-%m-%d")
        try:
            elo = self._get_scraper("ClubElo")
            df = elo.read_by_date(date_str)
            if df is None or df.empty:
                return []

            results = []
            for _, row in df.iterrows():
                results.append(EloRating(
                    team_name=str(row.get("team", row.get("Club", ""))),
                    rating=float(row.get("elo", row.get("Elo", 0))),
                    rank=int(row.get("rank", 0)) if pd.notna(row.get("rank")) else None,
                    date=datetime.strptime(date_str, "%Y-%m-%d"),
                    source="clubelo",
                ))
            logger.info(f"[soccerdata] ClubElo: fetched {len(results)} ratings for {date_str}")
            return results
        except Exception as e:
            logger.error(f"[soccerdata] ClubElo fetch failed: {e}")
            return []

    # ─── FBref ────────────────────────────────────
    def fetch_fbref_schedule(
        self,
        league: str = "INT-World Cup",
        season: str = "2022",
    ) -> pd.DataFrame:
        """获取 FBref 赛程/比分"""
        try:
            import soccerdata as sd
        except ImportError:
            return pd.DataFrame()

        try:
            fbref = self._get_scraper("FBref", league, season)
            df = fbref.read_schedule()
            logger.info(f"[soccerdata] FBref schedule: {len(df)} rows ({league} {season})")
            return df
        except Exception as e:
            logger.error(f"[soccerdata] FBref schedule failed: {e}")
            return pd.DataFrame()

    def fetch_fbref_team_stats(
        self,
        league: str = "INT-World Cup",
        season: str = "2022",
        stat_type: str = "standard",
    ) -> List[TeamStats]:
        """
        获取 FBref 球队赛季统计。
        stat_type: standard / passing / defense / gca / shooting / passing_types / goal_shooting_creation / misc
        """
        try:
            import soccerdata as sd
        except ImportError:
            return []

        try:
            fbref = self._get_scraper("FBref", league, season)
            df = fbref.read_team_season_stats(stat_type=stat_type)
            if df is None or df.empty:
                return []

            results = []
            for _, row in df.iterrows():
                # FBref 列名因 stat_type 而异，这里提取通用字段
                team = str(row.get("team", row.get("Squad", "")))
                results.append(TeamStats(
                    team_name=team,
                    season=season,
                    matches_played=_int_or_zero(row.get("MP")),
                    goals_for=_float_or_zero(row.get("Gls")),
                    goals_against=_float_or_zero(row.get("GA")),
                    xg_for=_float_or_zero(row.get("xG")),
                    xg_against=_float_or_zero(row.get("xGA")),
                    shots_per_game=_float_or_zero(row.get("Sh")),
                    possession=_float_or_zero(row.get("Poss")),
                    pass_completion=_float_or_zero(row.get("Cmp%")),
                    source=f"fbref-{stat_type}",
                ))
            logger.info(f"[soccerdata] FBref team stats ({stat_type}): {len(results)} teams")
            return results
        except Exception as e:
            logger.error(f"[soccerdata] FBref team stats failed: {e}")
            return []

    def fetch_fbref_player_stats(
        self,
        league: str = "INT-World Cup",
        season: str = "2022",
        stat_type: str = "standard",
    ) -> List[PlayerStats]:
        """获取 FBref 球员赛季统计"""
        try:
            import soccerdata as sd
        except ImportError:
            return []

        try:
            fbref = self._get_scraper("FBref", league, season)
            df = fbref.read_player_season_stats(stat_type=stat_type)
            if df is None or df.empty:
                return []

            results = []
            for _, row in df.iterrows():
                results.append(PlayerStats(
                    player_name=str(row.get("player", row.get("Player", ""))),
                    team_name=str(row.get("team", row.get("Squad", ""))),
                    season=season,
                    minutes=_int_or_zero(row.get("Min")),
                    goals=_int_or_zero(row.get("Gls")),
                    assists=_int_or_zero(row.get("Ast")),
                    xg=_float_or_zero(row.get("xG")),
                    xa=_float_or_zero(row.get("xAG")),
                    shots=_int_or_zero(row.get("Sh")),
                    key_passes=_int_or_zero(row.get("KP")),
                    yellow_cards=_int_or_zero(row.get("CrdY")),
                    red_cards=_int_or_zero(row.get("CrdR")),
                    source=f"fbref-{stat_type}",
                ))
            logger.info(f"[soccerdata] FBref player stats ({stat_type}): {len(results)} players")
            return results
        except Exception as e:
            logger.error(f"[soccerdata] FBref player stats failed: {e}")
            return []

    # ─── Football-Data.co.uk ──────────────────────
    def fetch_football_data_matches(
        self,
        league_code: str = "W1",   # W1 = World Cup
        season: str = "2425",      # SoccerData 使用两位年份格式
    ) -> List[HistoricalMatch]:
        """
        从 Football-Data.co.uk 获取历史比赛数据（含多博彩公司赔率）。
        league_code 示例: E0=英超, W1=世界杯, EC=欧洲杯
        """
        try:
            import soccerdata as sd
        except ImportError:
            return []

        try:
            fd = self._get_scraper("FootballData", league_code, season)
            df = fd.read_results()
            if df is None or df.empty:
                return []

            results = []
            for _, row in df.iterrows():
                # 多博彩公司赔率字段映射
                odds_map = {}
                for bm in ["B365", "BW", "IW", "PS", "WH", "VC"]:
                    h = row.get(f"{bm}H")
                    d = row.get(f"{bm}D")
                    a = row.get(f"{bm}A")
                    if pd.notna(h) and pd.notna(d) and pd.notna(a):
                        odds_map[bm.lower()] = {
                            "home": float(h), "draw": float(d), "away": float(a)
                        }

                hm = HistoricalMatch(
                    date=str(row.get("Date", "")),
                    home_team=str(row.get("HomeTeam", "")),
                    away_team=str(row.get("AwayTeam", "")),
                    home_goals=_int_or_none(row.get("FTHG")),
                    away_goals=_int_or_none(row.get("FTAG")),
                    odds_map=odds_map,
                    source="football-data",
                )
                # 取 Bet365 作为主赔率（或第一个可用的）
                if "b365" in odds_map:
                    hm.odds_home = odds_map["b365"]["home"]
                    hm.odds_draw = odds_map["b365"]["draw"]
                    hm.odds_away = odds_map["b365"]["away"]
                elif odds_map:
                    first = next(iter(odds_map.values()))
                    hm.odds_home = first["home"]
                    hm.odds_draw = first["draw"]
                    hm.odds_away = first["away"]
                results.append(hm)

            logger.info(f"[soccerdata] FootballData: {len(results)} matches ({league_code} {season})")
            return results
        except Exception as e:
            logger.error(f"[soccerdata] FootballData failed: {e}")
            return []

    # ─── Understat ────────────────────────────────
    def fetch_understat_team_xg(
        self,
        league: str = "EPL",
        season: str = "2022",
    ) -> pd.DataFrame:
        """获取 Understat 球队 xG 数据"""
        try:
            import soccerdata as sd
        except ImportError:
            return pd.DataFrame()

        try:
            us = self._get_scraper("Understat", league, season)
            df = us.read_team_xg()
            logger.info(f"[soccerdata] Understat team xG: {len(df)} rows")
            return df
        except Exception as e:
            logger.error(f"[soccerdata] Understat failed: {e}")
            return pd.DataFrame()

    # ─── WhoScored ────────────────────────────────
    def fetch_whoscored_ratings(
        self,
        league: str = "Premier League",
        season: str = "2023-2024",
    ) -> pd.DataFrame:
        """获取 WhoScored 球员评分"""
        try:
            import soccerdata as sd
        except ImportError:
            return pd.DataFrame()

        try:
            ws = self._get_scraper("WhoScored", league, season)
            # WhoScored 结构可能变化，先尝试通用方法
            df = ws.read_schedule() if hasattr(ws, "read_schedule") else pd.DataFrame()
            logger.info(f"[soccerdata] WhoScored: {len(df)} rows")
            return df
        except Exception as e:
            logger.error(f"[soccerdata] WhoScored failed: {e}")
            return pd.DataFrame()


# ─── 辅助函数 ──────────────────────────────────────
def _int_or_zero(val) -> int:
    try:
        return int(float(val)) if pd.notna(val) and val != "" else 0
    except (ValueError, TypeError):
        return 0


def _int_or_none(val) -> Optional[int]:
    try:
        return int(float(val)) if pd.notna(val) and val != "" else None
    except (ValueError, TypeError):
        return None


def _float_or_zero(val) -> float:
    try:
        return float(val) if pd.notna(val) and val != "" else 0.0
    except (ValueError, TypeError):
        return 0.0


# ─── 数据库同步接口 ────────────────────────────────
class SoccerDataSync:
    """
    将 SoccerData 抓取结果同步到本项目的 SQLite 数据库。
    与 models.py 中的 Team / Match / Player 表交互。
    """

    def __init__(self, db: Session):
        self.db = db
        self.client = SoccerDataClient()

    def sync_elo_ratings(self, date_str: Optional[str] = None) -> int:
        """
        同步 Club Elo 等级分到 teams 表。
        注意：Club Elo 主要是俱乐部；国家队需额外处理。
        """
        from models import Team

        ratings = self.client.fetch_elo_ratings(date_str)
        updated = 0
        for r in ratings:
            # 优先使用规范名匹配
            from data_cleaner import resolve_team_db
            team_id = resolve_team_db(self.db, r.team_name)
            if team_id:
                team = self.db.query(Team).filter(Team.id == team_id).first()
            else:
                # 降级到模糊匹配
                team = self.db.query(Team).filter(
                    Team.name.ilike(f"%{r.team_name}%")
                ).first()
            if team:
                team.elo = int(r.rating)
                updated += 1
        if updated:
            self.db.commit()
            logger.info(f"[sync] Updated Elo for {updated} teams")
        return updated

    def sync_fbref_team_stats(
        self,
        league: str = "INT-World Cup",
        season: str = "2022",
    ) -> int:
        """同步 FBref 球队统计到 teams 表（xG / possession 等）。"""
        from models import Team

        stats = self.client.fetch_fbref_team_stats(league, season, "standard")
        updated = 0
        for s in stats:
            team = self.db.query(Team).filter(
                Team.name.ilike(f"%{s.team_name}%")
            ).first()
            if team:
                # 只更新有数据的字段，保留旧值作为 fallback
                if s.xg_for:
                    team.avg_xg = round(s.xg_for / max(s.matches_played, 1), 2)
                if s.xg_against:
                    team.avg_xga = round(s.xg_against / max(s.matches_played, 1), 2)
                if s.possession:
                    team.possession = s.possession
                if s.pass_completion:
                    team.pass_completion = s.pass_completion
                if s.shots_per_game:
                    team.shots_per_game = s.shots_per_game
                team.stats_synced_at = datetime.utcnow()
                updated += 1
        if updated:
            self.db.commit()
            logger.info(f"[sync] Updated FBref team stats for {updated} teams")

        # 同时保留 JSON 缓存供回测使用
        if stats:
            cache_file = CACHE_DIR / f"fbref_team_{league.replace(' ', '_')}_{season}.json"
            data = [s.__dict__ for s in stats]
            cache_file.write_text(json.dumps(data, indent=2, default=str))
        return updated

    def sync_fbref_player_stats(
        self,
        league: str = "INT-World Cup",
        season: str = "2022",
    ) -> int:
        """同步 FBref 球员统计到 player_stats 表。"""
        from models import PlayerStats, Team

        stats = self.client.fetch_fbref_player_stats(league, season, "standard")
        inserted = 0
        for s in stats:
            # 查找对应球队
            team = self.db.query(Team).filter(
                Team.name.ilike(f"%{s.team_name}%")
            ).first()
            team_id = team.id if team else None

            # 简单去重：同名同队同赛季只保留一条
            existing = self.db.query(PlayerStats).filter(
                PlayerStats.player_name == s.player_name,
                PlayerStats.season == season,
                PlayerStats.team_id == team_id,
            ).first()

            if existing:
                existing.minutes = s.minutes
                existing.goals = s.goals
                existing.assists = s.assists
                existing.xg = s.xg
                existing.xa = s.xa
                existing.shots = s.shots
                existing.key_passes = s.key_passes
                existing.yellow_cards = s.yellow_cards
                existing.red_cards = s.red_cards
                existing.source = s.source
            else:
                ps = PlayerStats(
                    team_id=team_id,
                    player_name=s.player_name,
                    season=season,
                    league=league,
                    minutes=s.minutes,
                    goals=s.goals,
                    assists=s.assists,
                    xg=s.xg,
                    xa=s.xa,
                    shots=s.shots,
                    key_passes=s.key_passes,
                    yellow_cards=s.yellow_cards,
                    red_cards=s.red_cards,
                    source=s.source,
                )
                self.db.add(ps)
                inserted += 1

        self.db.commit()
        if inserted:
            logger.info(f"[sync] Inserted/updated {inserted} player stats from FBref")

        # 保留 JSON 缓存
        if stats:
            cache_file = CACHE_DIR / f"fbref_player_{league.replace(' ', '_')}_{season}.json"
            data = [s.__dict__ for s in stats]
            cache_file.write_text(json.dumps(data, indent=2, default=str))
        return inserted

    def sync_football_data_odds(
        self,
        league_code: str = "W1",
        season: str = "2425",
    ) -> List[HistoricalMatch]:
        """
        同步 Football-Data.co.uk 历史比赛+赔率到 match_bookmaker_odds 表。
        同时保留 JSON 缓存供回测框架读取。
        """
        from models import Match, MatchBookmakerOdds

        matches = self.client.fetch_football_data_matches(league_code, season)
        inserted = 0
        for hm in matches:
            # 尝试匹配数据库中的比赛（通过队名+日期模糊匹配）
            match = self.db.query(Match).filter(
                Match.home_team.has(name=hm.home_team),
                Match.away_team.has(name=hm.away_team),
            ).first()

            if match and hm.odds_map:
                for bm, odds in hm.odds_map.items():
                    existing = self.db.query(MatchBookmakerOdds).filter(
                        MatchBookmakerOdds.match_id == match.id,
                        MatchBookmakerOdds.bookmaker == bm,
                    ).first()
                    if not existing:
                        self.db.add(MatchBookmakerOdds(
                            match_id=match.id,
                            bookmaker=bm,
                            odds_home=odds["home"],
                            odds_draw=odds["draw"],
                            odds_away=odds["away"],
                            recorded_at=datetime.utcnow(),
                            is_closing=True,
                        ))
                        inserted += 1
        if inserted:
            self.db.commit()
            logger.info(f"[sync] Inserted {inserted} bookmaker odds records")

        # 保留 JSON 缓存
        if matches:
            cache_file = CACHE_DIR / f"fd_matches_{league_code}_{season}.json"
            data = [m.__dict__ for m in matches]
            cache_file.write_text(json.dumps(data, indent=2, default=str))
        return matches


# ─── CLI 测试入口 ──────────────────────────────────
def main():
    import argparse

    parser = argparse.ArgumentParser(description="SoccerData 集成测试")
    parser.add_argument("--source", choices=list(SOURCE_NAMES.keys()), default="fbref")
    parser.add_argument("--league", default="INT-World Cup")
    parser.add_argument("--season", default="2022")
    parser.add_argument("--date", help="Elo 日期 (YYYY-MM-DD)")
    args = parser.parse_args()

    client = SoccerDataClient()

    if args.source == "elo":
        ratings = client.fetch_elo_ratings(args.date)
        for r in ratings[:10]:
            print(f"  {r.team_name:<25} Elo={r.rating:.0f}")

    elif args.source == "fbref":
        print("\n📅 Schedule:")
        df = client.fetch_fbref_schedule(args.league, args.season)
        print(df.head())

        print("\n📊 Team Stats:")
        stats = client.fetch_fbref_team_stats(args.league, args.season)
        for s in stats[:5]:
            print(f"  {s.team_name:<20} GP={s.matches_played} GF={s.goals_for} xG={s.xg_for}")

        print("\n👤 Player Stats:")
        players = client.fetch_fbref_player_stats(args.league, args.season)
        for p in players[:5]:
            print(f"  {p.player_name:<20} {p.team_name:<15} Mins={p.minutes} G={p.goals} xG={p.xg}")

    elif args.source == "footballdata":
        matches = client.fetch_football_data_matches("W1", "2425")
        for m in matches[:5]:
            print(f"  {m.date} | {m.home_team} vs {m.away_team} | {m.home_goals}-{m.away_goals} | "
                  f"Odds: {m.odds_home}/{m.odds_draw}/{m.odds_away}")

    elif args.source == "understat":
        df = client.fetch_understat_team_xg("EPL", "2022")
        print(df.head())

    print("\n✅ SoccerData 测试完成")


if __name__ == "__main__":
    main()
