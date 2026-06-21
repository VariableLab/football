"""
世界杯预测引擎

包含：
  - Elo 实力模型
  - 泊松攻防模型（双变量）
  - 球员状态修正
  - 市场赔率隐含概率
  - 线性融合层
  - 回测框架

用法：
    engine = PredictionEngine()
    result = engine.predict(match, context)
    # result 包含全部 6 种玩法的概率分布
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Tuple, Optional, Any

import numpy as np
import pandas as pd

import os

# ─── 子模型已迁移到 features/ 包（向后兼容：本地定义仍可用）───
from core.models import EloModel, PoissonModel, PlayerAdjustmentModel, MarketModel, DrawDetectionModel
from features import (
    EloModel, PoissonModel, PlayerAdjustmentModel,
    MarketModel,
)

# ─── LR 融合层 (v2 架构) ───
# 注意：直接从 logistic_fusion 导入，不经过 fusion/__init__.py，避免循环导入
# (fusion/__init__.py → fusion_trainer.py → prediction_engine 循环)
import fusion.logistic_fusion as _lr_module
LogisticFusionWeights = _lr_module.LogisticFusionWeights
from features.feature_builder import FeatureBuilder
from features.form_markov_model import FormMarkovModel
from features.h2h_model import H2HModel

# ────────────────────────────
# 常量 — 从 core.constants 导入,避免循环导入
# ────────────────────────────
from core.constants import (
    MAX_GOALS, POISSON_TRUNCATE, HOME_ADVANTAGE_ELO, FORM_WINDOW_MATCHES,
    DIXON_COLES_RHO, DRAW_INFLATION_FACTOR, DEFAULT_WEIGHTS,
)

# 向后兼容: 保留模块级常量引用
_ENGINE_CFG = {
    "MAX_GOALS": MAX_GOALS,
    "POISSON_TRUNCATE": POISSON_TRUNCATE,
    "HOME_ADVANTAGE_ELO": HOME_ADVANTAGE_ELO,
    "FORM_WINDOW_MATCHES": FORM_WINDOW_MATCHES,
    "DIXON_COLES_RHO": DIXON_COLES_RHO,
    "DRAW_INFLATION_FACTOR": DRAW_INFLATION_FACTOR,
    "DEFAULT_WEIGHTS": DEFAULT_WEIGHTS,
}

# 向后兼容: load_engine_config() 别名
load_engine_config = lambda: _ENGINE_CFG


# ────────────────────────────
# 数据结构 — 迁移到 core/context.py
# ────────────────────────────
from core.context import (
    TeamContext, MatchContext, PredictionResult,
)

# ────────────────────────────
# 融合层
# ────────────────────────────
class EnsembleFusion:
    """线性加权融合，支持从历史数据学习动态权重"""

    def __init__(self, weights: Optional[Dict[str, float]] = None, db_session=None):
        self.weights = weights or DEFAULT_WEIGHTS.copy()
        self._db = db_session

    @staticmethod
    def _elo_diff_tier(elo_home: int, elo_away: int) -> str:
        diff = abs(elo_home - elo_away)
        if diff < 100:
            return "0-100"
        elif diff < 200:
            return "100-200"
        elif diff < 400:
            return "200-400"
        return "400+"

    @staticmethod
    def _stage_category(stage: str) -> str:
        """将具体阶段映射为 group / knockout / all"""
        if stage in ("group",):
            return "group"
        if stage in ("R32", "R16", "QF", "SF", "F", "3P", "knockout"):
            return "knockout"
        return "all"

    def _load_learned_weights(
        self, stage: str, elo_home: int, elo_away: int
    ) -> Optional[Dict[str, float]]:
        """从数据库加载最优权重，按 stage类别 + elo_diff 匹配"""
        if self._db is None:
            return None
        try:
            from database.models import FusionWeight

            elo_tier = self._elo_diff_tier(elo_home, elo_away)
            stage_cat = self._stage_category(stage)
            # 先尝试 stage类别 + elo_tier
            fw = (
                self._db.query(FusionWeight)
                .filter(
                    FusionWeight.stage == stage_cat,
                    FusionWeight.elo_diff_range == elo_tier,
                    FusionWeight.is_active == True,
                )
                .order_by(FusionWeight.learned_at.desc())
                .first()
            )
            if fw:
                return self._parse_weights(fw.weights)
            # 降级：只匹配 stage类别
            fw = (
                self._db.query(FusionWeight)
                .filter(
                    FusionWeight.stage == stage_cat,
                    FusionWeight.elo_diff_range == "all",
                    FusionWeight.is_active == True,
                )
                .order_by(FusionWeight.learned_at.desc())
                .first()
            )
            if fw:
                return self._parse_weights(fw.weights)
            # 降级：跨 stage 匹配 elo_tier
            fw = (
                self._db.query(FusionWeight)
                .filter(
                    FusionWeight.stage == "all",
                    FusionWeight.elo_diff_range == elo_tier,
                    FusionWeight.is_active == True,
                )
                .order_by(FusionWeight.learned_at.desc())
                .first()
            )
            if fw:
                return self._parse_weights(fw.weights)
            # 最终降级：全局权重
            fw = (
                self._db.query(FusionWeight)
                .filter(
                    FusionWeight.stage == "all",
                    FusionWeight.elo_diff_range == "all",
                    FusionWeight.is_active == True,
                )
                .order_by(FusionWeight.learned_at.desc())
                .first()
            )
            if fw:
                return self._parse_weights(fw.weights)
        except Exception as e:
            logger.warning(f"[fusion] Failed to load learned weights, using defaults: {e}")
        return None

    @staticmethod
    def _parse_weights(raw) -> Dict[str, float]:
        """解析权重，兼容 JSON 字符串和 dict"""
        if isinstance(raw, dict):
            return {k: float(v) for k, v in raw.items()}
        if isinstance(raw, str):
            import json as _json
            return {k: float(v) for k, v in _json.loads(raw).items()}
        return DEFAULT_WEIGHTS.copy()

    def get_weights(self, ctx: MatchContext) -> Dict[str, float]:
        """获取融合权重：优先学习权重 → 传入权重 → 默认权重"""
        if self._db is not None:
            learned = self._load_learned_weights(
                ctx.stage, ctx.home_team.elo, ctx.away_team.elo
            )
            if learned:
                return learned
        return self.weights.copy()

    def get_effective_weights(
        self, market: Optional[Dict[str, float]], ctx: Optional[MatchContext] = None
    ) -> Dict[str, float]:
        """获取实际使用的融合权重（含无赔率降级后的重新分配）"""
        w = self.get_weights(ctx) if ctx else self.weights.copy()
        if market is None:
            total = w["elo"] + w["poisson"] + w["players"]
            if total > 0:
                w = {
                    "elo": w["elo"] / total,
                    "poisson": w["poisson"] / total,
                    "players": w["players"] / total,
                    "market": 0.0,
                }
        return w


    def fuse_spf(
        self,
        elo: Dict[str, float],
        poisson: Dict[str, float],
        players: float,
        market: Optional[Dict[str, float]],
        ctx: Optional[MatchContext] = None,
    ) -> Dict[str, float]:
        """
        融合胜平负概率。
        players 是战力修正系数，其权重控制修正强度（权重越高，
        players_factor 对 elo/poisson 的缩放越强）。
        ctx 可选，用于动态加载学习权重。
        """
        w = self.get_weights(ctx) if ctx else self.weights.copy()

        # 动态权重调整：有真实竞彩/收盘赔率时提升 market 权重
        if market is not None and ctx is not None:
            has_real_odds = ctx.has_closing_odds or (ctx.odds_home and ctx.odds_home > 1.01)
            is_league = ctx.stage in ("group", "") and not ctx.is_knockout
            if has_real_odds and is_league:
                # 联赛有真实赔率：market 权重提升到 50%
                boost = 0.50 - w.get("market", 0)
                if boost > 0:
                    w["market"] = 0.50
                    # 从 elo 和 poisson 平均扣除
                    w["elo"] = max(w.get("elo", 0) - boost * 0.4, 0.05)
                    w["poisson"] = max(w.get("poisson", 0) - boost * 0.4, 0.10)
                    w["players"] = max(w.get("players", 0) - boost * 0.2, 0.05)
                    total_w = sum(w.values())
                    w = {k: v / total_w for k, v in w.items()}

        if market is None:
            # 没有市场赔率时，权重在 elo/poisson/players 之间重新分配
            total = w["elo"] + w["poisson"] + w["players"]
            w = {
                "elo": w["elo"] / total,
                "poisson": w["poisson"] / total,
                "players": w["players"] / total,
                "market": 0.0,
            }

        # players 权重控制 adjust 强度：权重越高，players_factor 修正越强
        # 当 players 权重为 0 时，blend_factor=1.0（无调整）；权重为 0.3 时，接近全幅度调整
        adjust_strength = min(1.0, w["players"] * 3.0)
        blend_factor = 1.0 + (players - 1.0) * adjust_strength

        def adjust(probs: Dict[str, float], factor: float) -> Dict[str, float]:
            # factor > 1 增强主队，factor < 1 增强客队
            home_adj = probs["home"] * factor
            away_adj = probs["away"] / factor if factor > 0 else probs["away"]
            draw_adj = probs["draw"]
            t = home_adj + draw_adj + away_adj
            return {"home": home_adj / t, "draw": draw_adj / t, "away": away_adj / t}

        elo_adj = adjust(elo, blend_factor)
        poisson_adj = adjust(poisson, blend_factor)

        result = {}
        for outcome in ["home", "draw", "away"]:
            val = (
                w["elo"] * elo_adj[outcome]
                + w["poisson"] * poisson_adj[outcome]
                + w["market"] * (market.get(outcome, 1 / 3.0) if market else 0)
            )
            result[outcome] = val

        # 归一化
        total = sum(result.values())
        return {k: max(0.001, v / total) for k, v in result.items()}

    @classmethod
    def fuse_probabilities(
        cls,
        base: Dict[str, float],
        modifier: Dict[str, float],
        alpha: float = 0.7
    ) -> Dict[str, float]:
        """通用概率融合：base 为基础，modifier 为修正"""
        result = {}
        keys = set(base.keys()) | set(modifier.keys())
        for k in keys:
            b = base.get(k, 0)
            m = modifier.get(k, 0)
            result[k] = alpha * b + (1 - alpha) * m
        total = sum(result.values())
        return {k: max(0, v / total) for k, v in result.items()}


# ────────────────────────────
# 主预测引擎
# ────────────────────────────
class PredictionEngine:
    """
    整合全部子模型，对外提供统一的 predict() 接口。
    """

    def __init__(self, weights: Optional[Dict[str, float]] = None, db_session=None,
                 use_lr_fusion: bool = True):
        self.db = db_session
        self.fusion = EnsembleFusion(weights, db_session=db_session)
        self.use_lr_fusion = use_lr_fusion
        self._lr_weights_cache: Dict[str, LogisticFusionWeights] = {}
        self._feature_builder = FeatureBuilder(use_interactions=True)
        if use_lr_fusion:
            # 预加载全局权重作为默认值
            global_w = self._load_lr_weights("global")
            if global_w:
                self._lr_weights_cache["global"] = global_w

    @property
    def _lr_weights(self) -> Optional["LogisticFusionWeights"]:
        """兼容属性：返回全局主权重"""
        return self._lr_weights_cache.get("global")

    @staticmethod
    def _load_lr_weights(league: str = "global") -> Optional["LogisticFusionWeights"]:
        """加载指定联赛的最新的 LR 融合权重，fallback 为 None"""
        import glob
        import os
        try:
            # 修正路径：从 backend/core 到 backend/data
            _root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            _lr_weights_dir = os.path.join(_root, "data", "weights", "lr")
            
            # 匹配模式: {league}_v1_*.json
            pattern = os.path.join(_lr_weights_dir, f"{league}_v1_*.json")
            lr_files = sorted(glob.glob(pattern))
            if lr_files:
                w = LogisticFusionWeights.load(lr_files[-1])
                import logging
                logging.getLogger("prediction_engine").info(
                    f"[LR-fusion] Loaded {league} weights: {os.path.basename(lr_files[-1])} "
                    f"(acc={w.accuracy:.1%}, n={w.sample_count})"
                )
                return w
        except Exception as e:
            import logging
            logging.getLogger("prediction_engine").warning(
                f"[LR-fusion] Failed to load {league} weights: {e}"
            )
        return None

    def _get_lr_weights_for_match(self, competition: str) -> Optional["LogisticFusionWeights"]:
        """动态路由：根据比赛所属联赛选择最优权重"""
        # 1. 尝试从缓存中获取
        if competition in self._lr_weights_cache:
            return self._lr_weights_cache[competition]

        # 2. 尝试从磁盘加载该联赛专属权重
        w = self._load_lr_weights(competition)
        if w:
            self._lr_weights_cache[competition] = w
            return w

        # 3. Fallback 到全局权重
        return self._lr_weights_cache.get("global")

    def _apply_live_odds_override(
        self, spf: Dict[str, float], ctx: MatchContext
    ) -> Dict[str, float]:
        """
        [意图识别层] 动态市场异动修正 (Steam Move Adjustment)
        
        不再使用死板的阈值拦截，而是使用平滑函数计算‘市场热值’。
        """
        if not ctx.has_closing_odds or not ctx.has_odds:
            return spf

        moves = {}
        for sel in ["home", "draw", "away"]:
            closing = getattr(ctx, f"closing_odds_{sel}")
            opening = getattr(ctx, f"odds_{sel}")
            if closing and opening:
                moves[sel] = (closing - opening) / opening
            else:
                moves[sel] = 0.0

        # 找出波动最剧烈的方向 (通常是跳水方向)
        best_move_sel = min(moves, key=moves.get)
        best_move_val = moves[best_move_sel]

        # 💡 使用 Sigmoid 激活函数计算修正强度 (Alpha)
        # 当跳水 > 5% 时开始介入，跳水 > 15% 时达到最高强度
        intensity = 1.0 / (1.0 + np.exp(-20 * (abs(best_move_val) - 0.10)))
        
        if abs(best_move_val) > 0.05 and best_move_val < 0:
            import logging
            logging.getLogger("prediction_engine").info(
                f"[Market-Signal] Intensity: {intensity:.2f} for {best_move_sel} move {best_move_val:+.1%}"
            )
            # 动态融合：根据市场异动的强度，将一部分概率分配给该方向
            # 这种方式比硬编码更科学，符合 Karpathy 的第一性原理
            target_prob = 0.65 if intensity > 0.5 else 0.50 # 简化的市场吸纳概率
            
            # 修正：(1-alpha)*Original + alpha*Signal
            alpha = 0.4 * intensity
            spf[best_move_sel] = (1 - alpha) * spf[best_move_sel] + alpha * target_prob
            
            # 归一化
            total = sum(spf.values())
            spf = {k: v / total for k, v in spf.items()}
            
        return spf

    def _recalibrate_scores(self, raw_score: Dict[str, float], fused_spf: Dict[str, float]) -> Dict[str, float]:
        """使用最终融合的胜平负概率，对比分预测进行贝叶斯校准"""
        calibrated = {}
        by_outcome = {"home": {}, "draw": {}, "away": {}}
        
        for score_key, prob in raw_score.items():
            try:
                parts = score_key.split(':')
                h = int(parts[0].replace('+', ''))
                a = int(parts[1].replace('+', ''))
                if h > a:
                    outcome = "home"
                elif h < a:
                    outcome = "away"
                else:
                    outcome = "draw"
            except:
                outcome = "draw"
            by_outcome[outcome][score_key] = prob
            
        sums = {k: sum(v.values()) for k, v in by_outcome.items()}
        
        for outcome, group in by_outcome.items():
            target_prob = fused_spf.get(outcome, 0.33)
            current_sum = sums[outcome]
            if current_sum > 0:
                for score_key, prob in group.items():
                    calibrated[score_key] = (prob / current_sum) * target_prob
            elif target_prob > 0:
                common_scores = {
                    "home": ["1:0", "2:0", "2:1"],
                    "draw": ["1:1", "0:0", "2:2"],
                    "away": ["0:1", "0:2", "1:2"]
                }[outcome]
                for cs in common_scores:
                    calibrated[cs] = target_prob / len(common_scores)
                    
        total = sum(calibrated.values())
        if total > 0:
            calibrated = {k: round(v / total, 4) for k, v in calibrated.items() if (v / total) >= 0.005}
        return calibrated

    def _recalibrate_goals(self, recal_score: Dict[str, float]) -> Dict[str, float]:
        """依据校准后的比分概率，重新生成总进球概率"""
        goals = {}
        for g in range(7):
            goals[str(g)] = 0.0
        goals["7+"] = 0.0
        
        for score_key, prob in recal_score.items():
            try:
                parts = score_key.split(':')
                h = int(parts[0].replace('+', ''))
                a = int(parts[1].replace('+', ''))
                total_g = h + a
                if total_g >= 7:
                    goals["7+"] += prob
                else:
                    goals[str(total_g)] += prob
            except:
                pass
                
        total = sum(goals.values())
        if total > 0:
            goals = {k: round(v / total, 4) for k, v in goals.items() if (v / total) > 0.002}
        return goals

    def _recalibrate_half(self, raw_half: Dict[str, float], fused_spf: Dict[str, float]) -> Dict[str, float]:
        """依据融合后的胜平负概率，校准半全场转移概率"""
        outcome_map = {
            "主主": "home", "平主": "home", "客主": "home",
            "主平": "draw", "平平": "draw", "客平": "draw",
            "主客": "away", "平客": "away", "客客": "away"
        }
        
        by_ft = {"home": {}, "draw": {}, "away": {}}
        for key, prob in raw_half.items():
            ft = outcome_map.get(key, "draw")
            by_ft[ft][key] = prob
            
        sums = {k: sum(v.values()) for k, v in by_ft.items()}
        calibrated = {}
        
        for ft, group in by_ft.items():
            target_prob = fused_spf.get(ft, 0.33)
            current_sum = sums[ft]
            if current_sum > 0:
                for key, prob in group.items():
                    calibrated[key] = (prob / current_sum) * target_prob
            elif target_prob > 0:
                common = [k for k, v in outcome_map.items() if v == ft]
                for c in common:
                    calibrated[c] = target_prob / len(common)
                    
        total = sum(calibrated.values())
        if total > 0:
            calibrated = {k: round(v / total, 4) for k, v in calibrated.items()}
        return calibrated

    def predict(self, ctx: MatchContext) -> PredictionResult:
        from core.logic_tracer import LogicChain
        trace = LogicChain(match_id=ctx.match_id)

        # ─── F1 Bridge: 尝试加载实验室验证的最强逻辑 ───
        lab_poisson_spf = None
        lab_elo_spf = None
        try:
            # 使用绝对寻址确保万无一失
            _cur_dir = os.path.dirname(os.path.abspath(__file__))
            _b_root = os.path.dirname(_cur_dir)
            
            # 导入实验室模型
            try:
                from core.research_poisson import PoissonPredictor as LabPoisson
                from core.research_elo import EloPredictor as LabElo
            except ImportError:
                # 兼容测试环境下的不同导入路径
                from research_poisson import PoissonPredictor as LabPoisson
                from research_elo import EloPredictor as LabElo

            p_weight_path = os.path.join(_b_root, "data", "weights", "research", "poisson_expert_weights.json")
            e_weight_path = os.path.join(_b_root, "data", "weights", "research", "elo_expert_weights.json")
            
            # 1. Lab Poisson
            if os.path.exists(p_weight_path):
                if not hasattr(PredictionEngine, "_lab_poisson_cache"):
                    PredictionEngine._lab_poisson_cache = LabPoisson()
                    PredictionEngine._lab_poisson_cache.load_params(p_weight_path)
                lab_p = PredictionEngine._lab_poisson_cache
                
                # 💡 关键修复：使用英文名匹配专家模型权重
                h_name = ctx.home_team.name_en or ctx.home_team.name
                a_name = ctx.away_team.name_en or ctx.away_team.name
                df_mock = pd.DataFrame([{"HomeTeam": h_name, "AwayTeam": a_name}])
                lab_p_res = lab_p.predict_proba(df_mock)
                # 防御性修复：防止 lab_p_res 为 None 或空
                if lab_p_res is not None and len(lab_p_res) > 0 and lab_p_res[0] is not None:
                    lab_poisson_spf = {"home": lab_p_res[0][0], "draw": lab_p_res[0][1], "away": lab_p_res[0][2]}
                    trace.add_step("Lab-Expert Poisson", "使用实验室 Dixon-Coles 泊松参数", lab_poisson_spf)
                else:
                    import logging
                    logging.getLogger("prediction_engine").info("Lab Poisson skipped: Team not found in weights")

            # 2. Lab Elo
            if os.path.exists(e_weight_path):
                if not hasattr(PredictionEngine, "_lab_elo_cache"):
                    PredictionEngine._lab_elo_cache = LabElo()
                    PredictionEngine._lab_elo_cache.load_params(e_weight_path)
                lab_e = PredictionEngine._lab_elo_cache

                # 💡 关键修复：使用英文名匹配专家模型权重
                h_name = ctx.home_team.name_en or ctx.home_team.name
                a_name = ctx.away_team.name_en or ctx.away_team.name
                df_mock = pd.DataFrame([{"HomeTeam": h_name, "AwayTeam": a_name}])
                lab_e_res = lab_e.predict_proba(df_mock)
                # 防御性修复：防止 lab_e_res 为 None 或空
                if lab_e_res is not None and len(lab_e_res) > 0 and lab_e_res[0] is not None:
                    lab_elo_spf = {"home": lab_e_res[0][0], "draw": lab_e_res[0][1], "away": lab_e_res[0][2]}
                    trace.add_step("Lab-Expert Elo", "使用实验室百年历史基准 Elo 参数", lab_elo_spf)

        except Exception as e:
            import logging
            logging.getLogger("prediction_engine").warning(f"[F1-Bridge] Lab injection failed: {e}")

        # 1. 跑各子模型
        elo_out = EloModel.predict(ctx)
        if lab_elo_spf:
            elo_out = lab_elo_spf

        # 如果有实验室泊松，优先使用
        poisson_out = PoissonModel.predict(ctx)
        if lab_poisson_spf:
            poisson_out["spf"] = lab_poisson_spf

        players_factor = PlayerAdjustmentModel.predict(ctx)
        market_out = MarketModel.predict(ctx)

        # 💡 数据反脆弱核心逻辑：识别并处理降级状态 (Degraded Mode)
        # 如果赔率来源是合成的 (synthetic) 或数据完全缺失，则视为处于断流状态
        is_degraded = False
        _odds_source = getattr(ctx, 'odds_source', getattr(ctx.odds, 'source', '')) if hasattr(ctx, 'odds') and ctx.odds else getattr(ctx, 'odds_source', '')
        if _odds_source == "synthetic" or market_out is None:
            is_degraded = True
            trace.add_step("数据断流预警", "检测到赔率数据源失效，系统自动进入 [反脆弱降级模式]：优先信任物理实力模型 (Elo/Poisson)", {"mode": "degraded", "source": _odds_source})

        # 2. 融合胜平负 ── 优先使用 LR 逻辑回归融合 (v2)
        lr_spf = None
        weights = self._get_lr_weights_for_match(ctx.competition)

        # 💡 诊断日志: 帮助排查 LR 融合 0% 部署问题
        import logging as _log
        _diag_logger = _log.getLogger("lr_diagnosis")
        _diag_logger.info(
            f"[LR-diag] match_id={ctx.match_id} competition='{ctx.competition}' "
            f"weights_loaded={'yes' if weights else 'NO'} "
            f"is_degraded={is_degraded} market_out={'present' if market_out else 'None'} "
            f"has_closing_odds={ctx.has_closing_odds} has_odds={ctx.has_odds}"
        )

        real_market = market_out if not is_degraded else None

        if weights and real_market is not None:
            lr_spf = self._predict_with_lr(
                ctx, elo_out, poisson_out, players_factor, real_market, weights
            )

        if lr_spf is not None:
            fused_spf = lr_spf
            trace.add_step("逻辑回归基准", f"使用 {ctx.competition or '全球'} 48维特征模型计算出的初始概率分布", fused_spf)
            if real_market:
                fused_spf = {
                    k: 0.5 * fused_spf[k] + 0.5 * real_market[k]
                    for k in ["home", "draw", "away"]
                }
                trace.add_step("市场共识校准", "将模型预测与机构赔率隐含概率按 50:50 融合", fused_spf)
        else:
            # fallback: 旧4参数线性加权 (EnsembleFusion)
            fused_spf = self.fusion.fuse_spf(
                elo=elo_out,
                poisson=poisson_out["spf"],
                players=players_factor,
                market=real_market,
                ctx=ctx,
            )
            mode_desc = "基础混合融合" if not is_degraded else "纯物理实力降级融合"
            trace.add_step(mode_desc, "使用 Elo + 泊松 4 参数模型生成预测基准", fused_spf)

        # 💡 强力校准 (全局覆盖)：如果有实验室专家 Elo，则强制赋予其 90% 的权重
        # 在降级模式下，这 90% 的权重保证了预测的物理准确性
        if lab_elo_spf:
            weight_factor = 0.95 if is_degraded else 0.90
            fused_spf = {
                k: weight_factor * lab_elo_spf[k] + (1-weight_factor) * fused_spf[k]
                for k in ["home", "draw", "away"]
            }
            # 归一化
            s_val = sum(fused_spf.values())
            fused_spf = {k: v / s_val for k, v in fused_spf.items()}
            trace.add_step("专家Elo降级增强", f"降级模式下将实力权重提升至 {weight_factor:.0%}", fused_spf)

        # 2b. 临场跳水修正 (New!)
        old_spf = fused_spf.copy()
        fused_spf = self._apply_live_odds_override(fused_spf, ctx)
        if fused_spf != old_spf:
            trace.add_step("临场异动修正", "检测到赔率剧烈跳水（Steam Move），强制对齐机构大额资金流向", fused_spf)

        # 2c. 平局检测修正：精细化校准
        fused_spf = DrawDetectionModel.predict(fused_spf, ctx, market_out)
        trace.add_step("平局概率微调", "利用 Draw-MLP 分类器针对高相关性特征进行平局偏置修正", fused_spf)

        # 2d. 残差 NN 修正：用 ResidualNet 修正 LR 系统性偏差
        if lr_spf is not None:
            fused_spf = self._apply_residual_correction(fused_spf, ctx, poisson_out, market_out)
            trace.add_step("利润导向 NN 修正", "神经网络通过残差学习捕捉市场错价空间，优化最终 ROI 期望", fused_spf)

        # 3. 让球：基于泊松输出，但用融合后的 spf 做最终归一化参考
        rq_raw = poisson_out["rq"].copy()
        # 让球概率的方向应与融合spf一致
        spf_direction = fused_spf["home"] - fused_spf["away"]
        rq_direction = rq_raw["home"] - rq_raw["away"]
        if spf_direction * rq_direction < 0:
            # 方向相反，取平均（保守处理）
            rq_raw["home"] = (rq_raw["home"] + fused_spf["home"]) / 2
            rq_raw["away"] = (rq_raw["away"] + fused_spf["away"]) / 2
            rq_raw["draw"] = 1 - rq_raw["home"] - rq_raw["away"]
        rq = {k: max(0.001, v) for k, v in rq_raw.items() if k != "handicap"}
        total = sum(rq.values())
        rq = {k: v / total for k, v in rq.items()}

        # 4. 比分 / 总进球 / 半全场：在降级模式或 SPF 融合校准后，对比分/衍生玩法进行贝叶斯概率对齐
        score = self._recalibrate_scores(poisson_out["score"], fused_spf)
        goals = self._recalibrate_goals(score)
        half = self._recalibrate_half(poisson_out["half"], fused_spf)

        # 5. 置信度判断
        confidence = self._compute_confidence(fused_spf, market_out, ctx)
        
        # 💡 核心增加：数据真实性审计 (Data Veracity Guard)
        # 如果主队或客队的 Elo/xG 均为默认填充值，说明预测不可靠
        is_mock_data = False
        if ctx.home_team.elo == 1600 or ctx.away_team.elo == 1600:
            is_mock_data = True
            
        if is_mock_data:
            confidence = "low"
            
        # ─── v3.0 一致性混合对齐引擎 ───
        shadow_data = None
        try:
            from core.shadow_engine import ShadowPredictor
            real_handicap = getattr(ctx, "handicap", 0) or 0
            shadow_data = ShadowPredictor.predict(ctx, real_handicap, target_spf=fused_spf)
        except Exception as e:
            import logging
            logging.getLogger("prediction_engine").warning(f"[Shadow] Predict failed: {e}")

        # ─── v3.0_classic 纯物理 Dixon-Coles 预测 ───
        classic_data = None
        try:
            from core.shadow_engine import ShadowPredictor
            real_handicap = getattr(ctx, "handicap", 0) or 0
            classic_data = ShadowPredictor.predict(ctx, real_handicap, target_spf=None)
        except Exception as e:
            import logging
            logging.getLogger("prediction_engine").warning(f"[Classic Engine] Predict failed: {e}")

        # ─── v4.0 深度学习时序 xG 引擎预测 ───
        deep_data = None
        try:
            from core.deep_frontier_nn import DeepFrontierPredictor
            from core.shadow_engine import ShadowPredictor
            df_predictor = DeepFrontierPredictor(db_session=self.db)
            if df_predictor.is_ready():
                from features.feature_builder import FeatureBuilder
                temp_builder = FeatureBuilder(use_interactions=False)
                # 使用局部变量构建 48 维静态特征向量
                static_feats = temp_builder.build(
                    elo_probs=elo_out,
                    poisson_result=poisson_out,
                    players_factor=players_factor,
                    market_probs=market_out,
                    form_features=None,
                    h2h_features=None,
                    ctx=ctx
                )
                # 预测 xG 并通过物理对齐推导
                lam_h_pred, lam_a_pred = df_predictor.predict_xg(self.db, ctx, static_feats[:48])
                real_handicap = getattr(ctx, "handicap", 0) or 0
                deep_data = ShadowPredictor.predict(ctx, real_handicap, target_spf=None, custom_lambdas=(lam_h_pred, lam_a_pred))
        except Exception as e:
            import logging
            logging.getLogger("prediction_engine").warning(f"[Deep Frontier] Predict failed: {e}")

        return PredictionResult(
            match_id=ctx.match_id,
            spf=fused_spf,
            rq=rq,
            score=score,
            goals=goals,
            half=half,
            raw_elo=elo_out,
            raw_poisson=poisson_out["spf"],
            raw_players=players_factor,
            raw_market=market_out or {},
            model_version="v2.0",
            confidence=confidence,
            odds_degraded=market_out is None or is_mock_data,
            weights_used={"_fusion": "lr_v2", **(lr_spf or {})} if lr_spf is not None else self.fusion.get_effective_weights(market_out, ctx),
            trace=trace,
            shadow_data=shadow_data,
            classic_data=classic_data,
            deep_data=deep_data,
        )

    def _predict_with_lr(self, ctx: MatchContext, elo_out: Dict[str, float], poisson_out: Dict, players_factor: float, market_out: Optional[Dict[str, float]], weights: "LogisticFusionWeights",) -> Optional[Dict[str, float]]:
        """使用 LR 逻辑回归融合预测 SPF。失败时返回 None，fallback 到旧融合。"""
        try:
            # 构建 FormMarkov + H2H 特征（需要 DB session）
            form_features = None
            h2h_features = None
            if self.fusion._db is not None:
                try:
                    fm = FormMarkovModel(self.fusion._db)
                    form_features = fm.compute(
                        ctx.home_team.recent_results,
                        ctx.home_team.team_id,
                        is_home=True,
                    )
                    hm = H2HModel(self.fusion._db)
                    h2h_features = hm.compute(
                        ctx.home_team.team_id,
                        ctx.away_team.team_id,
                    )
                except Exception:
                    pass  # Form/H2H 不可用时 FeatureBuilder 会填充默认值

            # 构建 43 维特征向量
            features = self._feature_builder.build(
                elo_probs=elo_out,
                poisson_result=poisson_out,
                players_factor=players_factor,
                market_probs=market_out,
                form_features=form_features,
                h2h_features=h2h_features,
                ctx=ctx,
            )

            # LR 推理
            lr_probs = weights.predict(features)
            return lr_probs

        except Exception as e:
            import logging
            logging.getLogger("prediction_engine").warning(
                f"[LR-fusion] predict failed, fallback to EnsembleFusion: {e}"
            )
            return None

    def _apply_residual_correction(
        self,
        spf: Dict[str, float],
        ctx: MatchContext,
        poisson_out: Dict,
        market_out: Optional[Dict[str, float]],
    ) -> Dict[str, float]:
        """使用 Stacking NN (v3) 修正融合概率。"""
        try:
            from core.residual_nn import StackingPredictor
            predictor = StackingPredictor()
            if not predictor.is_ready():
                return spf

            # 1. 获取 Layer 2 基础特征 (48维)
            form_features = None
            h2h_features = None
            if self.fusion._db is not None:
                try:
                    from features.form_markov_model import FormMarkovModel
                    from features.h2h_model import H2HModel
                    fm = FormMarkovModel(self.fusion._db)
                    form_features = fm.compute(ctx.home_team.recent_results, ctx.home_team.team_id)
                    hm = H2HModel(self.fusion._db)
                    h2h_features = hm.compute(ctx.home_team.team_id, ctx.away_team.team_id)
                except: pass

            base_feats = self._feature_builder.build(
                elo_probs=EloModel.predict(ctx),
                poisson_result=poisson_out,
                players_factor=PlayerAdjustmentModel.predict(ctx),
                market_probs=market_out,
                form_features=form_features,
                h2h_features=h2h_features,
                ctx=ctx
            )

            # 2. 拼接最终输入 (54维): Base(48) + LR_SPF(3) + Market(3)
            lr_arr = np.array([spf.get('home', 0.33), spf.get('draw', 0.33), spf.get('away', 0.33)], dtype=np.float32)
            mkt_arr = np.array([market_out.get('home', 0.33), market_out.get('draw', 0.33), market_out.get('away', 0.33)] if market_out else lr_arr, dtype=np.float32)
            
            full_input = np.concatenate([base_feats, lr_arr, mkt_arr])

            # 3. 执行 Stacking 预测
            stacking_spf = predictor.predict(full_input)
            
            if stacking_spf:
                # 💡 动态融合：Layer 2 (LR) 与 Layer 3 (NN) 采用 40/60 加权
                # NN 负责捕捉非线性残差，LR 负责保持基准稳定性
                final_spf = {
                    k: 0.4 * spf[k] + 0.6 * stacking_spf[k]
                    for k in ["home", "draw", "away"]
                }
                # 归一化
                total = sum(final_spf.values())
                return {k: v / total for k, v in final_spf.items()}

            return spf

        except Exception as e:
            import logging
            logging.getLogger("prediction_engine").warning(f"[StackingNN] correction failed: {e}")
            return spf

    @staticmethod
    def _compute_confidence(
        spf: Dict[str, float],
        market: Optional[Dict[str, float]],
        ctx: MatchContext,
    ) -> str:
        """
        [意图识别层] 基于信息熵与共识分歧的置信度评估。
        
        第一性原理：
        1. 熵 (Entropy) 越高，不确定性越大。
        2. 与市场分歧越大，风险越高。
        """
        probs = np.array([spf["home"], spf["draw"], spf["away"]])
        entropy = -np.sum(probs * np.log(probs + 1e-8))
        
        # 归一化熵 (0~1)，ln(3) 约为 1.098
        norm_entropy = entropy / 1.098
        
        agreement = 1.0
        if market:
            m_probs = np.array([market.get("home", 0.33), market.get("draw", 0.33), market.get("away", 0.33)])
            # 计算余弦相似度作为共识指标
            agreement = np.dot(probs, m_probs) / (np.linalg.norm(probs) * np.linalg.norm(m_probs))

        # 💡 置信度分级逻辑
        if norm_entropy < 0.4 and agreement > 0.95:
            return "high"
        if norm_entropy < 0.7 and agreement > 0.85:
            return "medium"
        return "low"


# ────────────────────────────
# 回测框架
# ────────────────────────────
@dataclass
class BacktestResult:
    """回测结果"""
    total_matches: int
    direction_accuracy: float          # 方向准确率（猜对胜平负）
    high_conf_accuracy: float          # 高置信度准确率
    brier_score: float                 # 概率校准度（越低越好）
    log_loss: float                    # 对数损失
    avg_max_prob: float                # 平均最高概率（反映模型自信度）
    weights: Dict[str, float]          # 最优权重


def brier_score(prob_true: float, outcome: int) -> float:
    """Brier Score: (prob - outcome)^2, outcome ∈ {0,1}"""
    return (prob_true - outcome) ** 2


def direction_correct(pred: Dict[str, float], actual: str) -> bool:
    """预测方向是否正确"""
    predicted = max(pred, key=pred.get)
    return predicted == actual


class Backtester:
    """
    历史回测：遍历历史比赛，用不同权重跑预测，评估指标。
    """

    def __init__(self, engine: PredictionEngine):
        self.engine = engine

    def evaluate_single(
        self,
        ctx: MatchContext,
        actual_outcome: str,   # "home" / "draw" / "away"
    ) -> Dict[str, float]:
        """评估单场比赛"""
        result = self.engine.predict(ctx)
        spf = result.spf

        # 方向
        correct = direction_correct(spf, actual_outcome)

        # Brier Score（对3个结果分别计算，取平均）
        bs = sum(
            brier_score(spf[k], 1 if actual_outcome == k else 0)
            for k in ["home", "draw", "away"]
        ) / 3.0

        # Log Loss（加平滑避免log(0)）
        prob = spf.get(actual_outcome, 1e-6)
        ll = -math.log(max(prob, 1e-6))

        return {
            "correct": float(correct),
            "brier": bs,
            "log_loss": ll,
            "max_prob": max(spf.values()),
            "confidence": result.confidence,
        }

    def run(
        self,
        historical_matches: List[Tuple[MatchContext, str]],
        weight_grids: Optional[Dict[str, List[float]]] = None,
    ) -> BacktestResult:
        """
        对历史数据跑回测，并网格搜索最优权重。

        historical_matches: [(MatchContext, actual_outcome), ...]
        """
        if weight_grids is None:
            # 默认网格：elo 0.1~0.5, poisson 0.2~0.6, players固定0.15, market 0~0.4
            weight_grids = {
                "elo": [0.1, 0.2, 0.3, 0.4, 0.5],
                "poisson": [0.2, 0.3, 0.4, 0.5, 0.6],
                "market": [0.0, 0.1, 0.2, 0.3, 0.4],
            }

        best_score = float("inf")
        best_weights = DEFAULT_WEIGHTS.copy()

        # 网格搜索（players 权重由剩余决定）
        for w_elo in weight_grids["elo"]:
            for w_poisson in weight_grids["poisson"]:
                for w_market in weight_grids["market"]:
                    w_players = 1.0 - w_elo - w_poisson - w_market
                    if w_players < 0 or w_players > 0.5:
                        continue

                    weights = {
                        "elo": w_elo,
                        "poisson": w_poisson,
                        "players": w_players,
                        "market": w_market,
                    }

                    # 跑回测
                    metrics = self._evaluate_weights(historical_matches, weights)
                    # 目标：最小化 Brier Score
                    score = metrics["avg_brier"]

                    if score < best_score:
                        best_score = score
                        best_weights = weights.copy()
                        best_metrics = metrics

        return BacktestResult(
            total_matches=len(historical_matches),
            direction_accuracy=best_metrics["accuracy"],
            high_conf_accuracy=best_metrics["high_conf_accuracy"],
            brier_score=best_metrics["avg_brier"],
            log_loss=best_metrics["avg_log_loss"],
            avg_max_prob=best_metrics["avg_max_prob"],
            weights=best_weights,
        )

    def _evaluate_weights(
        self,
        matches: List[Tuple[MatchContext, str]],
        weights: Dict[str, float],
    ) -> Dict[str, float]:
        """用给定权重跑全部历史比赛"""
        engine = PredictionEngine(weights=weights)
        results = []

        for ctx, actual in matches:
            try:
                r = engine.predict(ctx)
                spf = r.spf

                correct = direction_correct(spf, actual)
                bs = sum(
                    brier_score(spf[k], 1 if actual == k else 0)
                    for k in ["home", "draw", "away"]
                ) / 3.0
                ll = -math.log(max(spf.get(actual, 1e-6), 1e-6))

                results.append({
                    "correct": correct,
                    "brier": bs,
                    "log_loss": ll,
                    "max_prob": max(spf.values()),
                    "confidence": r.confidence,
                })
            except Exception:
                # 单场比赛失败不影响整体
                continue

        if not results:
            return {"accuracy": 0, "avg_brier": 1.0, "avg_log_loss": 10, "avg_max_prob": 0, "high_conf_accuracy": 0}

        corrects = [r["correct"] for r in results]
        briers = [r["brier"] for r in results]
        log_losses = [r["log_loss"] for r in results]
        max_probs = [r["max_prob"] for r in results]

        high_conf = [r for r in results if r["confidence"] == "high"]

        return {
            "accuracy": sum(corrects) / len(corrects),
            "avg_brier": sum(briers) / len(briers),
            "avg_log_loss": sum(log_losses) / len(log_losses),
            "avg_max_prob": sum(max_probs) / len(max_probs),
            "high_conf_accuracy": sum(r["correct"] for r in high_conf) / len(high_conf) if high_conf else 0,
        }


# ────────────────────────────
# ORM → TeamContext / MatchContext 构建
# ────────────────────────────
def build_team_context_from_orm(team) -> TeamContext:
    """从数据库 Team ORM 对象构建 TeamContext（供调度器/回测/seed 统一使用）。"""
    # possession → 战术风格推断校准
    tactical = team.tactical_style or "balanced"
    if team.possession and team.possession > 55 and tactical == "balanced":
        tactical = "attack"
    elif team.possession and team.possession < 45 and tactical == "balanced":
        tactical = "counter"

    return TeamContext(
        team_id=team.id,
        name=team.name,
        name_en=team.name_en or "",
        elo=team.elo or 1500,
        fifa_rank=team.fifa_rank or 100,
        avg_goals_scored=team.avg_goals_scored or 1.3,
        avg_goals_conceded=team.avg_goals_conceded or 1.3,
        avg_xg=team.avg_xg or 0.0,
        avg_xga=team.avg_xga or 0.0,
        possession=team.possession or 0.0,
        pass_completion=team.pass_completion or 0.0,
        shots_per_game=team.shots_per_game or 0.0,
        form_factor=team.form_factor or 1.0,
        recent_results=team.recent_results or "",
        recent_goals_scored=team.recent_goals_scored or 0.0,
        recent_goals_conceded=team.recent_goals_conceded or 0.0,
        home_away_factor=team.home_away_factor or 1.0,
        weather_adaptability=team.weather_adaptability or 1.0,
        tactical_style=tactical,
        coach_rating=team.coach_rating or 0.5,
        squad_fatigue_index=team.squad_fatigue_index or 0.5,
    )


def build_context_from_match(match, handicap: int = 0) -> MatchContext:
    """从数据库 Match ORM 对象构建完整的 MatchContext。"""
    home = build_team_context_from_orm(match.home_team)
    away = build_team_context_from_orm(match.away_team)
    # 判定是否为赛季冲刺阶段 (5月-6月)
    is_late = False
    if match.kickoff_at:
        is_late = match.kickoff_at.month in (5, 6)

    return MatchContext(
        match_id=match.id,
        home_team=home,
        away_team=away,
        kickoff_at=match.kickoff_at,
        stage=match.stage or "group",
        is_knockout=match.stage in ("R32", "R16", "QF", "SF", "F"),
        is_late_season=is_late,
        handicap=handicap,
        odds_home=match.odds_home,
        odds_draw=match.odds_draw,
        odds_away=match.odds_away,
        closing_odds_home=match.closing_odds_home,
        closing_odds_draw=match.closing_odds_draw,
        closing_odds_away=match.closing_odds_away,
        venue_type=match.venue_type or "neutral",
        weather=match.weather or "clear",
        temperature=match.temperature or 20.0,
        pitch_condition=match.pitch_condition or "good",
        schedule_density=match.schedule_density or "normal",
        competition=match.competition or "",
    )




# ────────────────────────────
# Mock 数据生成（用于测试）
# ────────────────────────────
def create_mock_context(
    match_id: int = 1,
    home_elo: int = 1985,
    away_elo: int = 1920,
    home_rank: int = 1,
    away_rank: int = 5,
    odds_home: float = 1.72,
    odds_draw: float = 3.40,
    odds_away: float = 4.80,
    stage: str = "group",
    is_knockout: bool = False,
) -> MatchContext:
    """生成测试用的 Mock 比赛上下文"""
    home = TeamContext(
        team_id=1,
        name="阿根廷",
        elo=home_elo,
        fifa_rank=home_rank,
        avg_goals_scored=1.80,
        avg_goals_conceded=0.70,
        form_factor=1.10,
        key_players_available=11,
        squad_fatigue_index=0.30,
    )
    away = TeamContext(
        team_id=2,
        name="巴西",
        elo=away_elo,
        fifa_rank=away_rank,
        avg_goals_scored=1.60,
        avg_goals_conceded=0.90,
        form_factor=1.05,
        key_players_available=10,
        squad_fatigue_index=0.40,
    )
    return MatchContext(
        match_id=match_id,
        home_team=home,
        away_team=away,
        stage=stage,
        is_knockout=is_knockout,
        odds_home=odds_home,
        odds_draw=odds_draw,
        odds_away=odds_away,
    )


# ────────────────────────────
# 投注策略引擎
# ────────────────────────────

@dataclass
class StrategyPick:
    """单个投注推荐"""
    strategy_name: str           # 策略名称
    strategy_type: str           # kelly / conservative / probability / ev_max / combo
    play_type: str               # spf / rq / score / goals / half
    play_label: str              # 玩法中文名
    selection: str               # 选项
    selection_label: str         # 选项中文名
    probability: float           # 模型概率
    odds: float                  # 赔率
    ev: float                    # 期望值 (prob * odds - 1)
    kelly_fraction: float        # 凯利比例
    stake_pct: float             # 建议投注比例 (%)
    confidence: str              # high / medium / low
    rationale: str               # 推荐理由
    risk_level: str              # low / medium / high


class BettingStrategy:
    """
    基于预测结果生成多维度投注策略。

    策略体系:
      1. 凯利准则 (kelly)      — 利益最大化，计算最优投注比例
      2. 保守策略 (conservative) — 风险最小，只选高置信+正EV
      3. 概率优先 (probability)  — 准确率最高，选概率最大
      4. EV最大化 (ev_max)       — 期望收益最高，选EV最大
      5. 组合策略 (combo)        — 综合评分最高，平衡收益与风险
    """

    # 玩法配置
    PLAY_CONFIG = {
        "SPF": {
            "label": "胜平负",
            "options": [
                ("home", "主胜"),
                ("draw", "平"),
                ("away", "客胜"),
            ],
            "odds_keys": ["odds_home", "odds_draw", "odds_away"],
        },
        "RQ": {
            "label": "让球",
            "options": [
                ("home", "让胜"),
                ("draw", "让平"),
                ("away", "让负"),
            ],
            "odds_keys": ["odds_home", "odds_draw", "odds_away"],
        },
        "SCORE": {
            "label": "比分",
            "options": None,  # 动态从概率中取 Top
            "odds_keys": [],
        },
        "GOALS": {
            "label": "总进球",
            "options": None,  # 动态
            "odds_keys": [],
        },
        "HALF": {
            "label": "半全场",
            "options": [
                ("home_home", "主主"),
                ("home_draw", "主平"),
                ("home_away", "主客"),
                ("draw_home", "平主"),
                ("draw_draw", "平平"),
                ("draw_away", "平客"),
                ("away_home", "客主"),
                ("away_draw", "客平"),
                ("away_away", "客客"),
            ],
            "odds_keys": [],
        },
    }

    def __init__(self, bankroll: float = 100.0, max_kelly: float = 0.25):
        """
        bankroll: 总资金（用于计算建议投注金额）
        max_kelly: 凯利比例上限（默认 25%，防止过度投注）
        """
        self.bankroll = bankroll
        self.max_kelly = max_kelly

    # ─── 工具方法 ───

    @staticmethod
    def calc_ev(prob: float, odds: float) -> float:
        return prob * odds - 1.0

    @staticmethod
    def calc_kelly(prob: float, odds: float) -> float:
        """凯利公式: f = (p*o - 1) / (o - 1)"""
        if odds <= 1.0:
            return 0.0
        k = (prob * odds - 1.0) / (odds - 1.0)
        return max(0.0, k)

    @staticmethod
    def score_to_odds(score_key: str) -> float:
        """比分赔率映射（简化模型，实际应由赔率采集提供）"""
        # 常见比分赔率参考（基于竞彩历史数据）
        odds_map = {
            "0:0": 8.0, "1:0": 6.0, "0:1": 7.0,
            "1:1": 6.5, "2:0": 7.5, "0:2": 9.0,
            "2:1": 7.5, "1:2": 8.5, "2:2": 13.0,
            "3:0": 11.0, "0:3": 16.0, "3:1": 10.0,
            "1:3": 13.0, "3:2": 14.0, "2:3": 16.0,
            "4:0": 18.0, "0:4": 28.0, "4:1": 15.0,
            "1:4": 22.0, "4:2": 22.0, "2:4": 28.0,
            "4:3": 35.0, "3:4": 45.0, "5:0": 35.0,
            "0:5": 60.0, "5:1": 30.0, "1:5": 50.0,
        }
        return odds_map.get(score_key, 25.0)

    @staticmethod
    def goals_to_odds(goals_key: str) -> float:
        """总进球赔率映射"""
        odds_map = {
            "0": 9.0, "1": 5.5, "2": 3.8, "3": 3.6,
            "4": 4.5, "5": 6.5, "6": 10.0, "7+": 12.0,
        }
        return odds_map.get(str(goals_key), 8.0)

    @staticmethod
    def half_to_odds(half_key: str) -> float:
        """半全场赔率映射"""
        odds_map = {
            "home_home": 3.0, "home_draw": 13.0, "home_away": 35.0,
            "draw_home": 5.0, "draw_draw": 5.5, "draw_away": 11.0,
            "away_home": 25.0, "away_draw": 13.0, "away_away": 4.0,
        }
        return odds_map.get(half_key, 15.0)

    def _get_odds(self, play_type: str, selection: str, match_odds: Dict[str, float]) -> float:
        """获取指定玩法的赔率"""
        if play_type == "SPF":
            return match_odds.get("odds_home", 2.0) if selection == "home" else \
                   match_odds.get("odds_draw", 3.2) if selection == "draw" else \
                   match_odds.get("odds_away", 3.5)
        elif play_type == "RQ":
            return match_odds.get("odds_home", 2.0) if selection == "home" else \
                   match_odds.get("odds_draw", 3.2) if selection == "draw" else \
                   match_odds.get("odds_away", 3.5)
        elif play_type == "SCORE":
            return self.score_to_odds(selection)
        elif play_type == "GOALS":
            return self.goals_to_odds(selection)
        elif play_type == "HALF":
            return self.half_to_odds(selection)
        return 2.0

    def _get_option_label(self, play_type: str, selection: str) -> str:
        """获取选项中文标签"""
        config = self.PLAY_CONFIG.get(play_type)
        if not config:
            return selection
        for key, label in (config["options"] or []):
            if key == selection:
                return label
        return selection

    def _confidence_score(self, confidence: str) -> float:
        return {"high": 1.0, "medium": 0.6, "low": 0.3}.get(confidence, 0.5)

    def _risk_level(self, prob: float, ev: float, confidence: str) -> str:
        """评估风险等级"""
        if confidence == "high" and prob > 0.55 and ev > 0.05:
            return "low"
        if confidence == "low" or prob < 0.35 or ev < -0.1:
            return "high"
        return "medium"

    # ─── 核心策略 ───

    def _kelly_strategy(self, candidates: List[Dict]) -> Optional[StrategyPick]:
        """凯利准则：选凯利比例最高的正EV选项"""
        best = None
        best_kelly = 0.0
        for c in candidates:
            if c["ev"] <= 0:
                continue
            k = self.calc_kelly(c["prob"], c["odds"])
            if k > best_kelly:
                best_kelly = k
                best = c
        if not best:
            return None
        stake = min(best_kelly, self.max_kelly) * 100  # 转化为百分比
        return self._build_pick("kelly", "凯利准则", best, stake,
                                f"凯利比例 {best_kelly:.1%}，期望收益最高")

    def _conservative_strategy(self, candidates: List[Dict], overall_confidence: str) -> Optional[StrategyPick]:
        """保守策略：只选高置信 + 正EV + 概率>50%"""
        if overall_confidence != "high":
            return None
        best = None
        best_ev = -999
        for c in candidates:
            if c["ev"] <= 0 or c["prob"] < 0.50:
                continue
            if c["ev"] > best_ev:
                best_ev = c["ev"]
                best = c
        if not best:
            return None
        return self._build_pick("conservative", "保守策略", best, 5.0,
                                "高置信+正EV，风险最低")

    def _probability_strategy(self, candidates: List[Dict]) -> Optional[StrategyPick]:
        """概率优先：选概率最大的"""
        best = max(candidates, key=lambda x: x["prob"])
        stake = min(10.0, max(2.0, best["prob"] * 15))  # 概率越高，投注越多
        return self._build_pick("probability", "概率优先", best, stake,
                                f"模型概率最高 {best['prob']:.1%}")

    def _ev_max_strategy(self, candidates: List[Dict]) -> Optional[StrategyPick]:
        """EV最大化：选期望收益最高的"""
        best = max(candidates, key=lambda x: x["ev"])
        if best["ev"] <= 0:
            # 如果没有正EV，选最接近0的
            best = max(candidates, key=lambda x: x["ev"])
        stake = min(12.0, max(2.0, best["ev"] * 30 + 5)) if best["ev"] > 0 else 2.0
        return self._build_pick("ev_max", "EV最大化", best, stake,
                                f"期望值 {'+' if best['ev']>0 else ''}{best['ev']:.1%}")

    def _combo_strategy(self, candidates: List[Dict], overall_confidence: str) -> Optional[StrategyPick]:
        """组合策略：综合评分 = 概率 * EV * 置信度权重"""
        conf_score = self._confidence_score(overall_confidence)
        best = None
        best_score = -999
        for c in candidates:
            # 综合评分: 概率 * max(EV, 0) * 置信度
            score = c["prob"] * max(c["ev"], 0) * conf_score
            if c["ev"] < 0:
                score = c["prob"] * (1 + c["ev"]) * conf_score * 0.5  # 负EV降权
            if score > best_score:
                best_score = score
                best = c
        if not best:
            return None
        stake = min(10.0, max(3.0, best["prob"] * best["odds"] * 3))
        ev_sign = "+" if best["ev"] > 0 else ""
        return self._build_pick("combo", "组合策略", best, stake,
                                "综合评分最优，概率×EV平衡")

    def _build_pick(self, stype: str, sname: str, c: Dict, stake: float, rationale: str) -> StrategyPick:
        risk = self._risk_level(c["prob"], c["ev"], c.get("confidence", "medium"))
        return StrategyPick(
            strategy_name=sname,
            strategy_type=stype,
            play_type=c["play_type"],
            play_label=self.PLAY_CONFIG[c["play_type"]]["label"],
            selection=c["selection"],
            selection_label=self._get_option_label(c["play_type"], c["selection"]),
            probability=c["prob"],
            odds=c["odds"],
            ev=c["ev"],
            kelly_fraction=self.calc_kelly(c["prob"], c["odds"]),
            stake_pct=round(stake, 1),
            confidence=c.get("confidence", "medium"),
            rationale=rationale,
            risk_level=risk,
        )

    # ─── 主入口 ───

    def generate(
        self,
        predictions: List[Dict[str, Any]],
        match_odds: Dict[str, float],
        overall_confidence: str = "medium",
    ) -> List[StrategyPick]:
        """
        基于预测结果生成全部策略推荐。

        predictions: 从 Prediction 表读取的 [{play_type, probabilities}, ...]
        match_odds:  {odds_home, odds_draw, odds_away}
        """
        # 1. 构建候选池（每个玩法的每个选项）
        candidates = []
        for pred in predictions:
            ptype = pred.get("play_type")
            probs = pred.get("probabilities", {})
            config = self.PLAY_CONFIG.get(ptype)
            if not config:
                continue

            # 比分/总进球 特殊处理：取 Top 选项
            if ptype in ("SCORE", "GOALS"):
                top_items = sorted(probs.items(), key=lambda x: x[1], reverse=True)[:5]
                for sel, prob in top_items:
                    odds = self._get_odds(ptype, sel, match_odds)
                    candidates.append({
                        "play_type": ptype,
                        "selection": sel,
                        "prob": prob,
                        "odds": odds,
                        "ev": self.calc_ev(prob, odds),
                        "confidence": overall_confidence,
                    })
            else:
                for sel, label in (config["options"] or []):
                    prob = probs.get(sel, 0)
                    if prob <= 0:
                        continue
                    odds = self._get_odds(ptype, sel, match_odds)
                    candidates.append({
                        "play_type": ptype,
                        "selection": sel,
                        "prob": prob,
                        "odds": odds,
                        "ev": self.calc_ev(prob, odds),
                        "confidence": overall_confidence,
                    })

        if not candidates:
            return []

        # 2. 运行各策略
        results = []

        kelly = self._kelly_strategy(candidates)
        if kelly:
            results.append(kelly)

        conservative = self._conservative_strategy(candidates, overall_confidence)
        if conservative:
            results.append(conservative)

        prob = self._probability_strategy(candidates)
        if prob:
            results.append(prob)

        ev_max = self._ev_max_strategy(candidates)
        if ev_max:
            results.append(ev_max)

        combo = self._combo_strategy(candidates, overall_confidence)
        if combo:
            results.append(combo)

        return results


# ────────────────────────────
# CLI 测试入口
# ────────────────────────────
if __name__ == "__main__":
    print("=" * 60)
    print("世界杯预测引擎 — 测试运行")
    print("=" * 60)

    # 1. 单场比赛预测
    ctx = create_mock_context()
    engine = PredictionEngine()
    result = engine.predict(ctx)

    print("\n【阿根廷 vs 巴西】预测结果")
    print(f"置信度: {result.confidence.upper()}")
    print(f"权重: {result.weights_used}")
    print("\n--- 胜平负 ---")
    for k, v in result.spf.items():
        print(f"  {k}: {v:.2%}")

    print("\n--- 让球(-1) ---")
    for k, v in result.rq.items():
        print(f"  {k}: {v:.2%}")

    print("\n--- 比分 TOP 5 ---")
    for score, prob in sorted(result.score.items(), key=lambda x: -x[1])[:5]:
        print(f"  {score}: {prob:.2%}")

    print("\n--- 总进球 ---")
    for g, prob in sorted(result.goals.items(), key=lambda x: int(x[0]) if x[0].isdigit() else 99)[:5]:
        print(f"  {g}球: {prob:.2%}")

    print("\n--- 半全场 TOP 5 ---")
    for h, prob in sorted(result.half.items(), key=lambda x: -x[1])[:5]:
        print(f"  {h}: {prob:.2%}")

    print("\n--- 模型拆解 ---")
    print(f"  Elo:       {result.raw_elo}")
    print(f"  Poisson:   {result.raw_poisson}")
    print(f"  Players:   {result.raw_players:.3f}")
    print(f"  Market:    {result.raw_market}")

    # 2. 简单回测演示（用模拟数据）
    print("\n" + "=" * 60)
    print("回测演示（模拟数据）")
    print("=" * 60)

    # 构造20场模拟历史比赛
    mock_history = []
    np.random.seed(42)
    for i in range(20):
        elo_diff = np.random.normal(50, 200)
        h_elo = 1800 + elo_diff / 2
        a_elo = 1800 - elo_diff / 2
        ctx = create_mock_context(
            match_id=i + 100,
            home_elo=int(h_elo),
            away_elo=int(a_elo),
            home_rank=max(1, int(50 - elo_diff / 40)),
            away_rank=max(1, int(50 + elo_diff / 40)),
            odds_home=2.0 - elo_diff / 400,
            odds_draw=3.2,
            odds_away=2.0 + elo_diff / 400,
        )
        # 模拟实际结果：Elo高的球队赢面大
        p_home = 1 / (1 + 10 ** (-(h_elo - a_elo) / 400))
        r = np.random.random()
        if r < p_home:
            actual = "home"
        elif r < p_home + 0.25:
            actual = "draw"
        else:
            actual = "away"
        mock_history.append((ctx, actual))

    backtester = Backtester(engine)
    bt_result = backtester.run(mock_history)

    print(f"\n回测结果（{bt_result.total_matches}场）")
    print(f"  方向准确率:       {bt_result.direction_accuracy:.2%}")
    print(f"  高置信度准确率:    {bt_result.high_conf_accuracy:.2%}")
    print(f"  Brier Score:      {bt_result.brier_score:.4f}  (越低越好, 随机=0.22)")
    print(f"  Log Loss:         {bt_result.log_loss:.4f}")
    print(f"  平均最高概率:      {bt_result.avg_max_prob:.2%}")
    print(f"  最优权重:         {bt_result.weights}")

    print("\n✅ 引擎测试完成")
