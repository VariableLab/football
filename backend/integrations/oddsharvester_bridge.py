"""
OddsHarvester (github.com/jordantete/OddsHarvester) 桥接模块

功能：
  1. 通过 subprocess 调用 OddsHarvester CLI，抓取 oddsportal.com 的历史/实时赔率
  2. 将抓取结果转换为本项目的 OddsSnapshot 格式
  3. 与 odds_collector.py 的 OddsSource 接口对齐

特点：
  - OddsHarvester 使用 Playwright 浏览器自动化，依赖较重
  - 推荐作为独立 Docker 服务运行，本项目通过 CLI 调用桥接
  - 支持 8 种运动、100+ 联赛，覆盖 1x2 / 让球 / 大小球 等市场

安装（独立环境）：
    pip install git+https://github.com/jordantete/OddsHarvester.git
    # 或 Docker:
    docker build -t oddsharvester .

许可证：MIT（与 OddsHarvester 一致）
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from logger import get_logger

logger = get_logger("oddsharvester")

# ─── 常量 ──────────────────────────────────────────
ODDSHARVESTER_CMD = shutil.which("oddsharvester") or "oddsharvester"
DEFAULT_OUTPUT_DIR = Path(__file__).parent.parent / ".oddsharvester_output"
DEFAULT_OUTPUT_DIR.mkdir(exist_ok=True)

# oddsportal 联赛 slug 映射（部分常用）
ODDSPORTAL_LEAGUES = {
    "world_cup": "soccer/world/world-cup",
    "euro": "soccer/world/european-championship",
    "epl": "soccer/england/premier-league",
    "laliga": "soccer/spain/laliga",
    "bundesliga": "soccer/germany/bundesliga",
    "serie_a": "soccer/italy/serie-a",
    "ligue_1": "soccer/france/ligue-1",
    "ucl": "soccer/europe/champions-league",
}


# ─── 数据结构 ──────────────────────────────────────
@dataclass
class OddsPortalMatch:
    """oddsportal 单场比赛赔率记录"""
    date: str
    home_team: str
    away_team: str
    home_goals: Optional[int]
    away_goals: Optional[int]
    # 1x2 平均赔率
    odds_home: Optional[float]
    odds_draw: Optional[float]
    odds_away: Optional[float]
    # 让球盘
    handicap: Optional[float] = None
    odds_home_hcp: Optional[float] = None
    odds_away_hcp: Optional[float] = None
    # 大小球
    over_under: Optional[float] = None
    odds_over: Optional[float] = None
    odds_under: Optional[float] = None
    # 元数据
    season: str = ""
    league: str = ""
    source: str = "oddsharvester"


# ─── CLI 调用器 ────────────────────────────────────
class OddsHarvesterCLI:
    """封装 OddsHarvester 的 CLI 调用"""

    def __init__(self, output_dir: Optional[Path] = None):
        self.output_dir = output_dir or DEFAULT_OUTPUT_DIR
        self.available = self._check_installation()

    def _check_installation(self) -> bool:
        """检查 oddsharvester 是否已安装"""
        try:
            result = subprocess.run(
                [ODDSHARVESTER_CMD, "--version"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            return result.returncode == 0
        except FileNotFoundError:
            logger.warning(
                f"[oddsharvester] CLI not found at '{ODDSHARVESTER_CMD}'. "
                "Install: pip install git+https://github.com/jordantete/OddsHarvester.git"
            )
            return False
        except Exception as e:
            logger.warning(f"[oddsharvester] Installation check failed: {e}")
            return False

    def _run(
        self,
        mode: str,          # upcoming / historic
        league: str,
        sport: str = "soccer",
        season: Optional[str] = None,
        preview_only: bool = False,
        output_format: str = "json",
    ) -> Optional[Path]:
        """
        运行 oddsharvester CLI，返回输出文件路径。
        """
        if not self.available:
            return None

        out_file = self.output_dir / f"{mode}_{league.replace('/', '_')}_{season or 'current'}.json"

        cmd = [
            ODDSHARVESTER_CMD,
            mode,
            "--sport", sport,
            "--league", league,
            "--output", str(out_file),
            "--format", output_format,
        ]
        if season:
            cmd.extend(["--season", season])
        if preview_only:
            cmd.append("--preview-only")

        logger.info(f"[oddsharvester] Running: {' '.join(cmd)}")
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=300,  # 5 分钟超时（Playwright 较慢）
            )
            if result.returncode != 0:
                logger.error(f"[oddsharvester] CLI failed: {result.stderr}")
                return None
            if out_file.exists():
                return out_file
            return None
        except subprocess.TimeoutExpired:
            logger.error("[oddsharvester] CLI timed out after 300s")
            return None
        except Exception as e:
            logger.error(f"[oddsharvester] CLI execution failed: {e}")
            return None

    def fetch_upcoming(
        self,
        league: str,
        preview_only: bool = True,
    ) -> List[OddsPortalMatch]:
        """
        抓取 upcoming 比赛赔率。
        preview_only=True 时只取平均赔率（速度快）。
        """
        out_file = self._run("upcoming", league, preview_only=preview_only)
        if not out_file:
            return []
        return self._parse_output(out_file)

    def fetch_historic(
        self,
        league: str,
        season: str,
        preview_only: bool = False,
    ) -> List[OddsPortalMatch]:
        """
        抓取历史赛季赔率（含赛果）。
        用于模型回测。数据量大，耗时较长。
        """
        out_file = self._run("historic", league, season=season, preview_only=preview_only)
        if not out_file:
            return []
        return self._parse_output(out_file)

    def _parse_output(self, file_path: Path) -> List[OddsPortalMatch]:
        """解析 OddsHarvester JSON/CSV 输出"""
        results = []
        try:
            if file_path.suffix == ".json":
                data = json.loads(file_path.read_text())
                # OddsHarvester JSON 结构可能变化，这里做防御式解析
                matches = data if isinstance(data, list) else data.get("matches", [])
                for m in matches:
                    results.append(OddsPortalMatch(
                        date=str(m.get("date", m.get("Date", ""))),
                        home_team=str(m.get("home", m.get("home_team", ""))),
                        away_team=str(m.get("away", m.get("away_team", ""))),
                        home_goals=m.get("home_goals") if m.get("home_goals") is not None else None,
                        away_goals=m.get("away_goals") if m.get("away_goals") is not None else None,
                        odds_home=_float_safe(m.get("odds_home", m.get("1"))),
                        odds_draw=_float_safe(m.get("odds_draw", m.get("X"))),
                        odds_away=_float_safe(m.get("odds_away", m.get("2"))),
                        season=str(m.get("season", "")),
                        league=str(m.get("league", "")),
                    ))
            else:
                # CSV 解析（简单实现）
                import csv
                with open(file_path) as f:
                    reader = csv.DictReader(f)
                    for row in reader:
                        results.append(OddsPortalMatch(
                            date=row.get("date", ""),
                            home_team=row.get("home_team", ""),
                            away_team=row.get("away_team", ""),
                            odds_home=_float_safe(row.get("odds_home")),
                            odds_draw=_float_safe(row.get("odds_draw")),
                            odds_away=_float_safe(row.get("odds_away")),
                        ))
        except Exception as e:
            logger.error(f"[oddsharvester] Parse failed for {file_path}: {e}")

        logger.info(f"[oddsharvester] Parsed {len(results)} matches from {file_path.name}")
        return results


# ─── 与 odds_collector.py 对齐的 OddsSource 适配器 ──
class OddsHarvesterSourceAdapter:
    """
    将 OddsHarvester 包装为与 odds_collector.OddsSource 兼容的接口。
    优先使用 cloakbrowser (Playwright stealth) 直接渲染 oddsportal 页面，
    降级到 OddsHarvester CLI，最终使用缓存文件。
    """

    name = "oddsharvester"

    def __init__(self):
        self.cli = OddsHarvesterCLI()
        self._cache: Dict[str, List[OddsPortalMatch]] = {}
        self._cloak: Optional[Any] = None

    def _get_cloak(self):
        """懒加载 cloakbrowser 桥接"""
        if self._cloak is None:
            try:
                from integrations.cloakbrowser_bridge import get_bridge
                bridge = get_bridge()
                if bridge.is_available():
                    self._cloak = bridge
                    logger.info("[oddsharvester] cloakbrowser available for oddsportal rendering")
            except Exception as e:
                logger.debug(f"[oddsharvester] cloakbrowser not available: {e}")
        return self._cloak

    def _fetch_oddsportal_via_cloak(self, league_slug: str) -> List[OddsPortalMatch]:
        """使用 cloakbrowser 直接渲染 oddsportal 赔率页面"""
        cloak = self._get_cloak()
        if not cloak:
            return []

        url = f"https://www.oddsportal.com/{league_slug}/"
        page = cloak.render_single(url, wait_selector=".table-main", wait_ms=3000)
        if not page or not page.html:
            return []

        from bs4 import BeautifulSoup
        soup = BeautifulSoup(page.html, "html.parser")
        matches = []

        for row in soup.select("table.table-main tr:not(.table-header)"):
            try:
                cols = row.select("td")
                if len(cols) < 4:
                    continue

                teams_el = cols[0].get_text(strip=True)
                if " - " not in teams_el:
                    continue
                home_team, away_team = teams_el.split(" - ", 1)

                odds_els = cols[1:4]
                odds_vals = []
                for el in odds_els:
                    try:
                        v = float(el.get_text(strip=True))
                        odds_vals.append(v if v > 1.0 else None)
                    except (ValueError, TypeError):
                        odds_vals.append(None)

                if len(odds_vals) >= 3 and all(v is not None for v in odds_vals):
                    matches.append(OddsPortalMatch(
                        date="",
                        home_team=home_team.strip(),
                        away_team=away_team.strip(),
                        home_goals=None,
                        away_goals=None,
                        odds_home=odds_vals[0],
                        odds_draw=odds_vals[1],
                        odds_away=odds_vals[2],
                        league=league_slug,
                    ))
            except Exception:
                continue

        if matches:
            logger.info(f"[oddsharvester] cloakbrowser parsed {len(matches)} matches from oddsportal/{league_slug}")
        return matches

    def _load_cache(self, league: str, season: Optional[str] = None):
        """从已抓取的输出文件加载缓存"""
        cache_key = f"{league}:{season or 'current'}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        # 尝试读取已存在的输出文件
        mode = "historic" if season else "upcoming"
        out_file = DEFAULT_OUTPUT_DIR / f"{mode}_{league.replace('/', '_')}_{season or 'current'}.json"
        if out_file.exists():
            matches = self.cli._parse_output(out_file)
            self._cache[cache_key] = matches
            return matches
        return []

    def fetch_historic_odds(
        self,
        league: str,
        season: str,
        force_refresh: bool = False,
    ) -> List[OddsPortalMatch]:
        """
        获取历史赛季赔率（含赛果）。
        优先级: cloakbrowser → OddsHarvester CLI → 缓存文件
        """
        cache_key = f"{league}:{season}"
        if not force_refresh:
            cached = self._load_cache(league, season)
            if cached:
                return cached

        # Try cloakbrowser first
        cloak_matches = self._fetch_oddsportal_via_cloak(league)
        if cloak_matches:
            self._cache[cache_key] = cloak_matches
            return cloak_matches

        # Fallback: OddsHarvester CLI
        matches = self.cli.fetch_historic(league, season, preview_only=False)
        self._cache[cache_key] = matches
        return matches

    def find_match_odds(
        self,
        league: str,
        season: str,
        home_team: str,
        away_team: str,
    ) -> Optional[OddsPortalMatch]:
        """在历史数据中查找特定比赛的赔率"""
        matches = self.fetch_historic_odds(league, season)
        home_l = home_team.lower()
        away_l = away_team.lower()
        for m in matches:
            if home_l in m.home_team.lower() and away_l in m.away_team.lower():
                return m
        return None

    # ─── OddsSource 接口实现 ────────────────────────
    def fetch(self, match) -> Optional[Any]:
        """
        实现 OddsSource.fetch() 接口。
        从已缓存的 OddsPortal 数据中查找单场比赛赔率。
        """
        from odds_collector import OddsSnapshot

        # 尝试从各联赛缓存中匹配
        home = (match.home_team.name if match.home_team else "").lower()
        away = (match.away_team.name if match.away_team else "").lower()

        for cache_key, matches in self._cache.items():
            for m in matches:
                if home in m.home_team.lower() and away in m.away_team.lower():
                    if m.odds_home and m.odds_draw and m.odds_away:
                        return OddsSnapshot(
                            match_id=match.id,
                            source=self.name,
                            odds_home=m.odds_home,
                            odds_draw=m.odds_draw,
                            odds_away=m.odds_away,
                            recorded_at=datetime.utcnow(),
                            handicap=m.handicap,
                            odds_home_hcp=m.odds_home_hcp,
                            odds_away_hcp=m.odds_away_hcp,
                        )
        return None

    def fetch_batch(self, matches) -> List[Any]:
        """
        实现 OddsSource.fetch_batch() 接口。
        批量从缓存中查找赔率。
        """
        results = []
        for match in matches:
            snap = self.fetch(match)
            if snap:
                results.append(snap)
        return results

    def download_all(self, use_cache: bool = True) -> List[Dict]:
        """导出所有缓存数据为 Dict 列表（供回测框架使用）"""
        all_matches = []
        for cache_key, matches in self._cache.items():
            for m in matches:
                all_matches.append({
                    "date": m.date,
                    "home_team": m.home_team,
                    "away_team": m.away_team,
                    "home_goals": m.home_goals,
                    "away_goals": m.away_goals,
                    "odds_home": m.odds_home,
                    "odds_draw": m.odds_draw,
                    "odds_away": m.odds_away,
                    "handicap": m.handicap,
                    "odds_home_hcp": m.odds_home_hcp,
                    "odds_away_hcp": m.odds_away_hcp,
                    "over_under": m.over_under,
                    "odds_over": m.odds_over,
                    "odds_under": m.odds_under,
                    "season": m.season,
                    "league": m.league,
                })
        return all_matches


# ─── 辅助函数 ──────────────────────────────────────
def _float_safe(val) -> Optional[float]:
    try:
        return float(val) if val is not None and val != "" else None
    except (ValueError, TypeError):
        return None


# ─── CLI 测试入口 ──────────────────────────────────
def main():
    import argparse

    parser = argparse.ArgumentParser(description="OddsHarvester 桥接测试")
    parser.add_argument("--mode", choices=["upcoming", "historic"], default="historic")
    parser.add_argument("--league", default="soccer/world/world-cup")
    parser.add_argument("--season", default="2022")
    parser.add_argument("--preview", action="store_true", help="preview-only mode (faster)")
    args = parser.parse_args()

    cli = OddsHarvesterCLI()
    if not cli.available:
        print("❌ OddsHarvester CLI 未安装")
        print("   安装: pip install git+https://github.com/jordantete/OddsHarvester.git")
        return

    if args.mode == "upcoming":
        matches = cli.fetch_upcoming(args.league, preview_only=args.preview)
    else:
        matches = cli.fetch_historic(args.league, args.season, preview_only=args.preview)

    print(f"\n📊 抓取到 {len(matches)} 场比赛:")
    for m in matches[:10]:
        print(f"  {m.date} | {m.home_team} vs {m.away_team} | "
              f"{m.home_goals}-{m.away_goals} | "
              f"Odds: {m.odds_home}/{m.odds_draw}/{m.odds_away}")

    print("\n✅ OddsHarvester 桥接测试完成")


if __name__ == "__main__":
    main()
