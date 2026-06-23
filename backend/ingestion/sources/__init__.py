"""
数据源包 — 赔率采集的数据源实现

每个数据源独立文件，通过 OddsCollector 统一管理。
"""
from ingestion.sources.base import OddsSnapshot, OddsSource
from ingestion.sources.dummy import _DummySource

__all__ = ["OddsSnapshot", "OddsSource", "_DummySource"]
