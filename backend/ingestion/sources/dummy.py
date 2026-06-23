"""
占位数据源 — 依赖未安装时的安全降级
"""
from typing import Any, List, Optional

from database.models import Match
from .base import OddsSnapshot, OddsSource


class _DummySource(OddsSource):
    """返回空结果的安全占位数据源"""

    def __init__(self, name: str):
        self.name = name

    def fetch(self, match: Match) -> Optional[OddsSnapshot]:
        return None

    def fetch_batch(self, matches: List[Match]) -> List[OddsSnapshot]:
        return []
