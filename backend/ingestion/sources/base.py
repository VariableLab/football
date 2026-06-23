"""
数据源基类 — OddsSource, OddsSnapshot
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional


@dataclass
class OddsSnapshot:
    """某一时刻的赔率快照"""
    match_id: int
    source: str                    # bet365 / macau / hkjc / jc / oddsapi / zgzcw
    odds_home: float
    odds_draw: float
    odds_away: float
    recorded_at: datetime

    # 让球（如有）
    handicap: Optional[float] = None
    odds_home_hcp: Optional[float] = None
    odds_away_hcp: Optional[float] = None

    # 多玩法赔率（竞彩专用，JSON 格式）
    multi_pool_odds: Optional[Dict[str, Any]] = None


class OddsSource(ABC):
    """赔率数据源抽象接口"""
    name: str = ""

    @abstractmethod
    def fetch(self, match: Any) -> Optional[OddsSnapshot]:
        """获取单场比赛的最新赔率"""
        pass

    @abstractmethod
    def fetch_batch(self, matches: List[Any]) -> List[OddsSnapshot]:
        """批量获取（效率更高）"""
        pass

    def download_all(self, use_cache: bool = True) -> List[Dict]:
        """下载全部数据（仅部分源支持，如 football-data）"""
        return []
