"""
赔率采集中心 — 向后兼容入口

所有数据源类已拆分至 ingestion/sources/ 目录。
此模块保持原有导入路径兼容：
  from odds_collector import (
      OddsCollector, OddsAnomaly, OddsApiBudget,
      collect_odds_tier1_primary, collect_odds_tier2_premium,
      collect_odds_tier3_focus, collect_closing_odds_for_upcoming,
      collect_odds_for_upcoming_matches, _get_upcoming_matches,
      FootballDataSource, OddsApiSource, JingcaiSource,
      MacauSource, HKJCSource, SyntheticOddsSource,
      BetExplorerSource, _DummySource, _fetch_with_retry,
  )
"""

# ── 核心类 ──
from ingestion.collector import (
    OddsCollector,
    OddsAnomaly,
    OddsApiBudget,
    collect_odds_tier1_primary,
    collect_odds_tier2_premium,
    collect_odds_tier3_focus,
    collect_closing_odds_for_upcoming,
    collect_odds_for_upcoming_matches,
    _get_upcoming_matches,
)

# ── 数据源（别名，供旧代码直接导入） ──
from ingestion.sources.football_data import FootballDataSource
from ingestion.sources.odds_api import OddsApiSource
from ingestion.sources.jingcai import JingcaiSource
from ingestion.sources.macau import MacauSource, HKJCSource
from ingestion.sources.synthetic import SyntheticOddsSource
from ingestion.sources.betexplorer import BetExplorerSource
from ingestion.sources.dummy import _DummySource
from ingestion.sources.football_data import _fetch_with_retry

# ── 基础类 ──
from ingestion.sources.base import OddsSnapshot, OddsSource

__all__ = [
    "OddsCollector", "OddsAnomaly", "OddsApiBudget",
    "OddsSnapshot", "OddsSource",
    "collect_odds_tier1_primary", "collect_odds_tier2_premium",
    "collect_odds_tier3_focus", "collect_closing_odds_for_upcoming",
    "collect_odds_for_upcoming_matches", "_get_upcoming_matches",
    "FootballDataSource", "OddsApiSource", "JingcaiSource",
    "MacauSource", "HKJCSource", "SyntheticOddsSource",
    "BetExplorerSource", "_DummySource", "_fetch_with_retry",
]
