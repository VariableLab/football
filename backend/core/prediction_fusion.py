"""
预测融合层 — EnsembleFusion 类

负责 Elo/Poisson/Players/Market 四参数的线性加权融合，
支持从历史数据学习的动态权重加载。
"""
from __future__ import annotations

from typing import Dict, Optional

from core.constants import DEFAULT_WEIGHTS


class EnsembleFusion:
    """线性加权融合，支持从历史数据学习动态权重"""

    def __init__(self, weights: Optional[Dict[str, float]] = None, db_session=None):
        self.weights = weights or DEFAULT_WEIGHTS.copy()
        self._db = db_session

    @staticmethod
    def _elo_diff_tier(elo_home: int, elo_away: int) -> str:
        diff = abs(elo_home - elo_away)
        if diff < 100: return "0-100"
        elif diff < 200: return "100-200"
        elif diff < 400: return "200-400"
        return "400+"

    @staticmethod
    def _stage_category(stage: str) -> str:
        if stage in ("group",): return "group"
        if stage in ("R32", "R16", "QF", "SF", "F", "3P", "knockout"): return "knockout"
        return "all"

    def _load_learned_weights(self, stage: str, elo_home: int, elo_away: int) -> Optional[Dict[str, float]]:
        if self._db is None: return None
        try:
            from database.models import FusionWeight
            elo_tier = self._elo_diff_tier(elo_home, elo_away)
            stage_cat = self._stage_category(stage)
            for s, e in [(stage_cat, elo_tier), (stage_cat, "all"), ("all", elo_tier), ("all", "all")]:
                fw = self._db.query(FusionWeight).filter(
                    FusionWeight.stage == s, FusionWeight.elo_diff_range == e,
                    FusionWeight.is_active == True,
                ).order_by(FusionWeight.learned_at.desc()).first()
                if fw:
                    return self._parse_weights(fw.weights)
        except Exception as e:
            import logging
            logging.getLogger("prediction_engine").warning(f"[fusion] Failed to load learned weights: {e}")
        return None

    @staticmethod
    def _parse_weights(raw) -> Dict[str, float]:
        if isinstance(raw, dict):
            return {k: float(v) for k, v in raw.items()}
        if isinstance(raw, str):
            import json
            return {k: float(v) for k, v in json.loads(raw).items()}
        return DEFAULT_WEIGHTS.copy()

    def get_weights(self, ctx) -> Dict[str, float]:
        if self._db is not None:
            learned = self._load_learned_weights(ctx.stage, ctx.home_team.elo, ctx.away_team.elo)
            if learned: return learned
        return self.weights.copy()

    def get_effective_weights(self, market: Optional[Dict[str, float]], ctx = None) -> Dict[str, float]:
        w = self.get_weights(ctx) if ctx else self.weights.copy()
        if market is None:
            total = w["elo"] + w["poisson"] + w["players"]
            if total > 0:
                w = {"elo": w["elo"]/total, "poisson": w["poisson"]/total, "players": w["players"]/total, "market": 0.0}
        return w

    def fuse_spf(self, elo: Dict[str, float], poisson: Dict[str, float], players: float,
                 market: Optional[Dict[str, float]], ctx = None) -> Dict[str, float]:
        """融合胜平负概率"""
        w = self.get_weights(ctx) if ctx else self.weights.copy()
        if market is not None and ctx is not None:
            has_real_odds = ctx.has_closing_odds or (ctx.odds_home and ctx.odds_home > 1.01)
            is_league = ctx.stage in ("group", "") and not ctx.is_knockout
            if has_real_odds and is_league:
                boost = 0.50 - w.get("market", 0)
                if boost > 0:
                    w["market"] = 0.50
                    w["elo"] = max(w.get("elo", 0) - boost * 0.4, 0.05)
                    w["poisson"] = max(w.get("poisson", 0) - boost * 0.4, 0.10)
                    w["players"] = max(w.get("players", 0) - boost * 0.2, 0.05)
                    total_w = sum(w.values())
                    w = {k: v / total_w for k, v in w.items()}

        if market is None:
            total = w["elo"] + w["poisson"] + w["players"]
            w = {"elo": w["elo"]/total, "poisson": w["poisson"]/total, "players": w["players"]/total, "market": 0.0}

        adjust_strength = min(1.0, w["players"] * 3.0)
        blend_factor = 1.0 + (players - 1.0) * adjust_strength

        def adjust(probs, factor):
            ha = probs["home"] * factor
            aa = probs["away"] / factor if factor > 0 else probs["away"]
            t = ha + probs["draw"] + aa
            return {"home": ha/t, "draw": probs["draw"]/t, "away": aa/t}

        elo_adj = adjust(elo, blend_factor)
        poisson_adj = adjust(poisson, blend_factor)
        result = {}
        for outcome in ["home", "draw", "away"]:
            val = (w["elo"] * elo_adj[outcome] + w["poisson"] * poisson_adj[outcome]
                   + w["market"] * (market.get(outcome, 1/3.0) if market else 0))
            result[outcome] = val
        total = sum(result.values())
        return {k: max(0.001, v / total) for k, v in result.items()}

    @classmethod
    def fuse_probabilities(cls, base: Dict[str, float], modifier: Dict[str, float], alpha: float = 0.7) -> Dict[str, float]:
        """通用概率融合"""
        result = {}
        for k in set(base.keys()) | set(modifier.keys()):
            result[k] = alpha * base.get(k, 0) + (1 - alpha) * modifier.get(k, 0)
        total = sum(result.values())
        return {k: max(0, v / total) for k, v in result.items()}
