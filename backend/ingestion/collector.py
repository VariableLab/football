"""
赔率采集核心 — OddsCollector, OddsAnomaly, OddsApiBudget

数据源类在 ingestion/sources/ 目录下，此模块负责：
- 预算管理 (OddsApiBudget)
- 异动检测 (OddsAnomaly)
- 采集调度 (OddsCollector)
"""
import json
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from database.models import Match, OddsHistory
from utils.logger import get_logger
from data_source.base import OddsSnapshot, OddsSource

logger = get_logger("odds.collector")


# ────────────────────────────
# 数据结构
# ────────────────────────────
@dataclass
class OddsAnomaly:
    """赔率异动告警"""
    match_id: int
    source: str
    direction: str                 # home / draw / away
    old_odds: float
    new_odds: float
    change_pct: float
    severity: str                  # info / warning / critical


# ────────────────────────────
# Odds API 预算管理器
# ────────────────────────────
class OddsApiBudget:
    """管理 Odds API 免费套餐的 500 credits/月预算"""
    FREE_MONTHLY_CREDITS = 500
    BUDGET_FILE = Path(__file__).parent / ".odds_api_budget.json"

    def __init__(self):
        self._lock = threading.Lock()
        self._data: dict[str, int | str] = {"year_month": "", "used": 0}
        self._load()

    def _load(self):
        if self.BUDGET_FILE.exists():
            try:
                self._data = json.loads(self.BUDGET_FILE.read_text())
            except Exception as e:
                logger.warning(f"[odds-budget] Corrupt budget file, resetting: {e}")
                self._data = {"year_month": "", "used": 0}
        self._reset_if_new_month()

    def _reset_if_new_month(self):
        current_ym = datetime.now(timezone.utc).strftime("%Y-%m")
        if self._data.get("year_month") != current_ym:
            self._data = {"year_month": current_ym, "used": 0}
            self._save()
            logger.info(f"[odds-budget] New month reset: {current_ym}, credits reset to 0")

    def _save(self):
        try:
            self.BUDGET_FILE.write_text(json.dumps(self._data))
        except Exception as e:
            logger.error(f"[odds-budget] Failed to save budget: {e}")

    def can_spend(self, amount: int = 1) -> bool:
        with self._lock:
            self._reset_if_new_month()
            return self._data["used"] + amount <= self.FREE_MONTHLY_CREDITS

    def spend(self, amount: int = 1) -> bool:
        with self._lock:
            self._reset_if_new_month()
            if self._data["used"] + amount > self.FREE_MONTHLY_CREDITS:
                logger.warning(f"[odds-budget] Budget exhausted: {self._data['used']}/{self.FREE_MONTHLY_CREDITS}")
                return False
            self._data["used"] += amount
            self._save()
            logger.info(f"[odds-budget] Spent {amount} credit(s), remaining: {self.remaining()}")
            return True

    def remaining(self) -> int:
        with self._lock:
            self._reset_if_new_month()
            return self.FREE_MONTHLY_CREDITS - self._data["used"]

    def status(self) -> Dict:
        self._reset_if_new_month()
        return {
            "month": self._data["year_month"],
            "used": self._data["used"],
            "remaining": self.remaining(),
            "total": self.FREE_MONTHLY_CREDITS,
        }


# ────────────────────────────
# 采集调度器
# ────────────────────────────
class OddsCollector:
    """
    统一管理多个数据源的赔率采集。
    分级策略：
      Tier 1 (Primary):  football-data 缓存 + 数据新鲜度检查（每2小时）
      Tier 2 (Premium):  Odds API 全量采集（每天2次）
      Tier 3 (Focus):    Odds API 焦点战加采（每天1次 + 赛前4h自动）
    """

    def __init__(self, db: Session, budget: Optional[OddsApiBudget] = None):
        self.db = db
        self.sources: Dict[str, OddsSource] = {}
        self.budget = budget or OddsApiBudget()
        self._init_sources()

    def _init_sources(self):
        """初始化所有可用的数据源"""
        self.sources["oddsapi"] = self._init_oddsapi()
        self.sources["football-data"] = self._init_football_data()
        self.sources["jingcai"] = self._init_jingcai()
        self.sources["macau"] = self._init_macau()
        self.sources["hkjc"] = self._init_hkjc()
        self.sources["betexplorer"] = self._init_betexplorer()
        self.sources["oddsharvester"] = self._init_oddsharvester()
        self.sources["synthetic"] = self._init_synthetic()
        self.sources["zgzcw"] = self._init_zgzcw()
        self.sources["500"] = self._init_wubaibai()

    def _init_oddsapi(self):
        try:
            from sources.odds_api import OddsApiSource
            return OddsApiSource()
        except Exception as e:
            logger.warning(f"[oddsapi] Failed to initialize: {e}")
            from sources.dummy import _DummySource
            return _DummySource("oddsapi")

    def _init_football_data(self):
        try:
            from sources.football_data import FootballDataSource
            return FootballDataSource()
        except Exception as e:
            logger.warning(f"[football-data] Failed to initialize: {e}")
            from sources.dummy import _DummySource
            return _DummySource("football-data")

    def _init_jingcai(self):
        try:
            from sources.jingcai import JingcaiSource
            return JingcaiSource()
        except Exception as e:
            logger.warning(f"[jingcai] Failed to initialize: {e}")
            from sources.dummy import _DummySource
            return _DummySource("jingcai")

    def _init_macau(self):
        try:
            from sources.macau import MacauSource
            return MacauSource()
        except Exception as e:
            logger.warning(f"[macau] Failed to initialize: {e}")
            from sources.dummy import _DummySource
            return _DummySource("macau")

    def _init_hkjc(self):
        try:
            from sources.macau import HKJCSource
            return HKJCSource()
        except Exception as e:
            logger.warning(f"[hkjc] Failed to initialize: {e}")
            from sources.dummy import _DummySource
            return _DummySource("hkjc")

    def _init_betexplorer(self):
        try:
            from sources.betexplorer import BetExplorerSource
            return BetExplorerSource()
        except Exception as e:
            logger.warning(f"[betexplorer] Failed to initialize: {e}")
            from sources.dummy import _DummySource
            return _DummySource("betexplorer")

    def _init_oddsharvester(self):
        try:
            from integrations.oddsharvester_bridge import OddsHarvesterSourceAdapter
            adapter = OddsHarvesterSourceAdapter()
            adapter._load_cache("soccer/world/world-cup", "2022")
            cloak = adapter._get_cloak()
            if cloak:
                logger.info("[oddsharvester] Adapter initialized with cloakbrowser")
            else:
                logger.info("[oddsharvester] Adapter initialized (cloakbrowser not available)")
            return adapter
        except Exception as e:
            logger.warning(f"[oddsharvester] Failed to initialize: {e}")
            from sources.dummy import _DummySource
            return _DummySource("oddsharvester")

    def _init_synthetic(self):
        try:
            from sources.synthetic import SyntheticOddsSource
            return SyntheticOddsSource()
        except Exception as e:
            logger.warning(f"[synthetic] Failed to initialize: {e}")
            from sources.dummy import _DummySource
            return _DummySource("synthetic")

    def _init_zgzcw(self):
        try:
            from ingestion.zgzcw_source import ZgzcwOddsSource
            source = ZgzcwOddsSource()
            logger.info("[zgzcw] Source initialized")
            return source
        except Exception as e:
            logger.warning(f"[zgzcw] Failed to initialize: {e}")
            from sources.dummy import _DummySource
            return _DummySource("zgzcw")

    def _init_wubaibai(self):
        try:
            from ingestion.wubaibai_source import WubaibaiOddsSource
            source = WubaibaiOddsSource()
            logger.info("[500] Source initialized")
            return source
        except Exception as e:
            logger.warning(f"[500] Failed to initialize: {e}")
            from sources.dummy import _DummySource
            return _DummySource("500")

    # ── Tier 采集 ──

    def collect_tier1_primary(self, matches: List[Match]) -> Dict:
        """Tier 1: 免费/基础层 — 每2小时"""
        stale_matches = 0
        updated_count = 0

        fd_source = self.sources.get("football-data")
        if fd_source:
            try:
                fd_source.download_all(use_cache=True)
                logger.info("[collect-tier1] football-data cache refreshed")
            except Exception as e:
                logger.error(f"[collect-tier1] football-data failed: {e}")

        batch_results = self.collect_batch(matches)

        for match in matches:
            sources = batch_results.get(match.id, {})
            if not sources:
                stale_matches += 1
                continue
            self.update_match_primary_odds(match, sources)
            updated_count += 1

        return {
            "stale_matches": stale_matches,
            "updated_count": updated_count,
            "total_matches": len(matches),
            "budget_remaining": self.budget.remaining(),
        }

    def collect_tier2_premium(self, matches: List[Match]) -> Dict:
        """Tier 2: Odds API 全量采集 — 每天2次"""
        if not self.budget.can_spend(1):
            logger.warning("[collect-tier2] Odds API budget exhausted")
            return {"skipped": True, "reason": "budget_exhausted", "credits_used": 0, "budget_remaining": 0}

        oddsapi = self.sources.get("oddsapi")
        if not oddsapi:
            return {"skipped": True, "reason": "oddsapi_not_configured", "credits_used": 0, "budget_remaining": 0}

        snapshots = oddsapi.fetch_batch(matches)
        if not self.budget.spend(1):
            return {"skipped": True, "reason": "budget_spend_failed", "credits_used": 0, "budget_remaining": 0}

        all_anomalies = []
        for snap in snapshots:
            match = next((m for m in matches if m.id == snap.match_id), None)
            if match:
                self.update_match_primary_odds(match, {"oddsapi": snap})
                anomalies = self.detect_anomalies(match, {"oddsapi": snap})
                all_anomalies.extend(anomalies)

        return {
            "matches_count": len(snapshots),
            "credits_used": 1,
            "budget_remaining": self.budget.remaining(),
            "anomalies": all_anomalies,
        }

    def collect_tier3_focus(self, matches: List[Match]) -> Dict:
        """Tier 3: 焦点战加采 — 每天1次 + 赛前4h自动"""
        now = datetime.now(timezone.utc)
        focus_matches = [
            m for m in matches
            if m.kickoff_at and 0 < ((m.kickoff_at.replace(tzinfo=timezone.utc) if m.kickoff_at.tzinfo is None else m.kickoff_at) - now).total_seconds() <= 4 * 3600
        ]

        if not focus_matches:
            logger.info("[collect-tier3] No focus matches within 4h")
            return {"skipped": True, "reason": "no_focus_matches"}
        if not self.budget.can_spend(1):
            logger.warning("[collect-tier3] Odds API budget exhausted")
            return {"skipped": True, "reason": "budget_exhausted", "credits_used": 0, "budget_remaining": 0}

        oddsapi = self.sources.get("oddsapi")
        if not oddsapi:
            return {"skipped": True, "reason": "oddsapi_not_configured"}

        snapshots = oddsapi.fetch_batch(focus_matches)
        self.budget.spend(1)

        all_anomalies = []
        for snap in snapshots:
            match = next((m for m in focus_matches if m.id == snap.match_id), None)
            if match:
                self.update_match_primary_odds(match, {"oddsapi-focus": snap})
                anomalies = self.detect_anomalies(match, {"oddsapi-focus": snap})
                all_anomalies.extend(anomalies)

        return {
            "matches_count": len(snapshots),
            "credits_used": 1,
            "budget_remaining": self.budget.remaining(),
            "anomalies": all_anomalies,
        }

    # ── 单场/批量采集 ──

    def collect_for_match(self, match: Match) -> Dict[str, OddsSnapshot]:
        """为单场比赛采集全部可用数据源的赔率"""
        results = {}
        has_real_odds = False

        real_sources = ["zgzcw", "500", "oddsapi", "football-data", "betexplorer", "jingcai", "macau", "hkjc"]
        for name in real_sources:
            source = self.sources.get(name)
            if not source:
                continue
            try:
                snapshot = source.fetch(match)
                if snapshot and all(v is not None for v in [snapshot.odds_home, snapshot.odds_draw, snapshot.odds_away]):
                    results[name] = snapshot
                    self._store_snapshot(snapshot, is_closing=False)
                    has_real_odds = True
                    logger.info(f"[collect] {match.match_code} | {name}: {snapshot.odds_home}/{snapshot.odds_draw}/{snapshot.odds_away}")
            except Exception as e:
                logger.warning(f"[collect] {match.match_code} | {name} failed: {e}")

        if not has_real_odds:
            synth = self.sources.get("synthetic")
            if synth:
                try:
                    snapshot = synth.fetch(match)
                    if snapshot and all(v is not None for v in [snapshot.odds_home, snapshot.odds_draw, snapshot.odds_away]):
                        results["synthetic"] = snapshot
                        self._store_snapshot(snapshot, is_closing=False)
                        logger.info(f"[collect] {match.match_code} | synthetic: {snapshot.odds_home}/{snapshot.odds_draw}/{snapshot.odds_away}")
                except Exception as e:
                    logger.warning(f"[collect] {match.match_code} | synthetic failed: {e}")

        return results

    def collect_batch(self, matches: List[Match]) -> Dict[int, Dict[str, OddsSnapshot]]:
        """批量采集（优先使用各数据源的批量接口）"""
        results: Dict[int, Dict[str, OddsSnapshot]] = {m.id: {} for m in matches}
        matched_ids: set[int] = set()

        real_sources = ["zgzcw", "500", "oddsapi", "football-data", "betexplorer", "jingcai", "macau", "hkjc"]
        for name in real_sources:
            source = self.sources.get(name)
            if not source:
                continue
            try:
                snapshots = source.fetch_batch(matches)
                for snap in snapshots:
                    if snap and all(v is not None for v in [snap.odds_home, snap.odds_draw, snap.odds_away]):
                        results[snap.match_id][name] = snap
                        self._store_snapshot(snap, is_closing=False)
                        matched_ids.add(snap.match_id)
                logger.info(f"[collect-batch] {name}: {len(snapshots)} matches")
            except Exception as e:
                logger.warning(f"[collect-batch] {name} failed: {e}")

        unmatched = [m for m in matches if m.id not in matched_ids]
        if unmatched:
            synth = self.sources.get("synthetic")
            if synth:
                try:
                    snapshots = synth.fetch_batch(unmatched)
                    for snap in snapshots:
                        if snap and all(v is not None for v in [snap.odds_home, snap.odds_draw, snap.odds_away]):
                            results[snap.match_id]["synthetic"] = snap
                            self._store_snapshot(snap, is_closing=False)
                    logger.info(f"[collect-batch] synthetic: {len(snapshots)} matches (fallback)")
                except Exception as e:
                    logger.warning(f"[collect-batch] synthetic fallback failed: {e}")

        return results

    # ── 赔率更新与异动检测 ──

    def update_match_primary_odds(self, match: Match, sources: Dict[str, OddsSnapshot], is_closing: bool = False):
        """将多源赔率汇总后，更新 Match 表的主赔率字段"""
        if not sources:
            return

        homes, draws, aways, source_names = [], [], [], []
        for name, snap in sources.items():
            homes.append(snap.odds_home)
            draws.append(snap.odds_draw)
            aways.append(snap.odds_away)
            source_names.append(name)

        if homes and draws and aways:
            match.odds_home = round(sum(homes) / len(homes), 2)
            match.odds_draw = round(sum(draws) / len(draws), 2)
            match.odds_away = round(sum(aways) / len(aways), 2)
            real = [n for n in source_names if n != "synthetic"]
            match.odds_source = real[0] if real else "synthetic"

        if is_closing:
            real_sources = {n: s for n, s in sources.items() if n != "synthetic"}
            if real_sources:
                r_homes = [s.odds_home for s in real_sources.values()]
                r_draws = [s.odds_draw for s in real_sources.values()]
                r_aways = [s.odds_away for s in real_sources.values()]
                match.closing_odds_home = round(sum(r_homes) / len(r_homes), 2)
                match.closing_odds_draw = round(sum(r_draws) / len(r_draws), 2)
                match.closing_odds_away = round(sum(r_aways) / len(r_aways), 2)
                match.closing_odds_source = list(real_sources.keys())[0]
                match.odds_locked_at = datetime.now(timezone.utc)
                logger.info(
                    f"[odds-closing] {match.match_code}: "
                    f"{match.closing_odds_home}/{match.closing_odds_draw}/{match.closing_odds_away} "
                    f"(source: {match.closing_odds_source})"
                )

        self.db.commit()
        if not is_closing:
            logger.info(f"[odds-update] {match.match_code}: {match.odds_home}/{match.odds_draw}/{match.odds_away} (source: {match.odds_source})")

        try:
            from core.prediction_recalc import on_odds_updated
            on_odds_updated(self.db, match.id)
        except Exception as e:
            logger.warning(f"[odds-update] Prediction recalc trigger failed: {e}")

    def detect_anomalies(self, match: Match, new_sources: Dict[str, OddsSnapshot],
                         threshold: float = 0.10) -> List[OddsAnomaly]:
        """检测赔率异动。变化超过 threshold（10%）则告警"""
        anomalies = []
        prev = {"home": match.odds_home, "draw": match.odds_draw, "away": match.odds_away}
        if not all(v is not None for v in prev.values()):
            return anomalies

        for source_name, snap in new_sources.items():
            for direction, new_val, old_val in [
                ("home", snap.odds_home, prev["home"]),
                ("draw", snap.odds_draw, prev["draw"]),
                ("away", snap.odds_away, prev["away"]),
            ]:
                if old_val is None or old_val == 0 or new_val is None:
                    continue
                change = abs(new_val - old_val) / old_val
                if change > threshold:
                    severity = "critical" if change > 0.20 else "warning" if change > 0.10 else "info"
                    anomalies.append(OddsAnomaly(
                        match_id=match.id, source=source_name, direction=direction,
                        old_odds=old_val, new_odds=new_val, change_pct=change, severity=severity,
                    ))
        return anomalies

    def _store_snapshot(self, snapshot: OddsSnapshot, is_closing: bool = False):
        """存储赔率快照到 OddsHistory 表，自动去重 (5min窗口)"""
        from ingestion.data_cleaner import validate_source, validate_odds
        source = validate_source(snapshot.source)
        is_real = source != "synthetic"

        h, d, a, valid = validate_odds(snapshot.odds_home, snapshot.odds_draw, snapshot.odds_away)
        if not valid:
            return

        cutoff = snapshot.recorded_at - timedelta(minutes=5)
        exists = self.db.query(OddsHistory).filter(
            OddsHistory.match_id == snapshot.match_id,
            OddsHistory.source == snapshot.source,
            OddsHistory.recorded_at >= cutoff,
        ).first()
        if exists:
            return

        history = OddsHistory(
            match_id=snapshot.match_id, source=source,
            odds_home=snapshot.odds_home, odds_draw=snapshot.odds_draw, odds_away=snapshot.odds_away,
            recorded_at=snapshot.recorded_at, is_closing=is_closing, is_real=is_real,
        )
        self.db.add(history)
        self.db.commit()

    def collect_closing_odds(self, matches: List[Match]) -> Dict:
        """采集收盘赔率 — 赛前最后一批真实赔率。赛前 15~60 分钟调用"""
        now = datetime.now(timezone.utc)
        closing_matches = []
        for m in matches:
            if not m.kickoff_at:
                continue
            k_at = m.kickoff_at
            if k_at.tzinfo is None:
                k_at = k_at.replace(tzinfo=timezone.utc)
            if 0 < (k_at - now).total_seconds() <= 90 * 60:
                closing_matches.append(m)

        if not closing_matches:
            return {"skipped": True, "reason": "no_closing_window_matches"}

        results = []
        real_sources = ["zgzcw", "500", "oddsapi", "football-data", "betexplorer", "jingcai", "macau", "hkjc"]
        for match in closing_matches:
            best_snap = None
            best_source = None
            for name in real_sources:
                source = self.sources.get(name)
                if not source:
                    continue
                try:
                    snap = source.fetch(match)
                    if snap and all(v is not None for v in [snap.odds_home, snap.odds_draw, snap.odds_away]):
                        best_snap = snap
                        best_source = name
                        break
                except Exception as e:
                    logger.debug(f"[closing-odds] {match.match_code} | {name} failed: {e}")

            if best_snap:
                self._store_snapshot(best_snap, is_closing=True)
                self.update_match_primary_odds(match, {best_source: best_snap}, is_closing=True)
                results.append(match.match_code)
            else:
                logger.warning(f"[closing-odds] {match.match_code}: no real odds available")

        return {
            "matches_processed": len(closing_matches),
            "matches_updated": len(results),
            "updated_codes": results,
        }


# ────────────────────────────
# 向后兼容：旧版独立函数接口
# ────────────────────────────
def _get_upcoming_matches(db: Session, hours: int = 72) -> List[Match]:
    """获取未来 N 小时内未锁定的比赛（用于赔率采集）"""
    from database.models import Match
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    return (
        db.query(Match)
        .filter(
            Match.status.in_(["scheduled", "live"]),
            Match.odds_locked_at.is_(None),
            Match.kickoff_at > cutoff,
        )
        .all()
    )


def collect_odds_tier1_primary(db: Session) -> Dict:
    """Tier 1: 免费/基础层赔率采集"""
    matches = _get_upcoming_matches(db, hours=48)
    if not matches:
        return {"skipped": True, "reason": "no_upcoming_matches"}
    collector = OddsCollector(db)
    return collector.collect_tier1_primary(matches)


def collect_odds_tier2_premium(db: Session) -> Dict:
    """Tier 2: Odds API 全量采集"""
    matches = _get_upcoming_matches(db, hours=48)
    if not matches:
        return {"skipped": True, "reason": "no_upcoming_matches"}
    collector = OddsCollector(db)
    return collector.collect_tier2_premium(matches)


def collect_odds_tier3_focus(db: Session) -> Dict:
    """Tier 3: 焦点战加采"""
    matches = _get_upcoming_matches(db, hours=48)
    if not matches:
        return {"skipped": True, "reason": "no_upcoming_matches"}
    collector = OddsCollector(db)
    return collector.collect_tier3_focus(matches)


def collect_closing_odds_for_upcoming(db: Session, hours: int = 4) -> Dict:
    """采集收盘赔率"""
    matches = _get_upcoming_matches(db, hours=hours)
    if not matches:
        return {"skipped": True, "reason": "no_upcoming_matches"}
    collector = OddsCollector(db)
    return collector.collect_closing_odds(matches)


def collect_odds_for_upcoming_matches(db: Session, hours: int = 72) -> Dict:
    """全量采集：Tier 1 + Tier 2 + Tier 3"""
    matches = _get_upcoming_matches(db, hours=hours)
    if not matches:
        return {"skipped": True, "reason": "no_upcoming_matches"}
    collector = OddsCollector(db)
    tier1 = collector.collect_tier1_primary(matches)
    tier2 = collector.collect_tier2_premium(matches)
    tier3 = collector.collect_tier3_focus(matches)
    return {
        "tier1": tier1,
        "tier2": tier2,
        "tier3": tier3,
    }
