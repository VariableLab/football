"""
优化方案验证框架 — 系统化回测对比 + 统计显著性检验

设计原则:
1. 每个方案有3个核心指标，明确定义计算方式
2. 调整前后逐指标对比，含赔率区间细分拆解
3. 量化"优化成功"标准，自动判定 PASS/FAIL
4. 支持统计显著性检验，防止小样本偶然

验证流程:
  baseline → 逐方案回测 → 指标对比 → 统计检验 → 自动判定
"""
import json
import math
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from strategy_config import StrategyParams, load_params, compute_position_ratio
from tiered_strategy import (
    TIER_HIGH, TIER_MEDIUM, TIER_SKIP,
    classify_tier, select_spf_recommendation,
)
from utils.logger import get_logger

logger = get_logger("validation_framework")

VALIDATION_DIR = "./data/strategy/validation"
os.makedirs(VALIDATION_DIR, exist_ok=True)


# ────────────────────────────
# 数据结构
# ────────────────────────────

@dataclass(frozen=True)
class OddsBucketStats:
    """单个赔率区间的统计"""
    odds_min: float
    odds_max: float
    count: int = 0
    correct: int = 0
    hit_rate: float = 0.0
    roi: float = 0.0
    profit: float = 0.0
    stakes: float = 0.0
    breakeven_rate: float = 0.0  # 该赔率区间的盈亏平衡命中率


@dataclass(frozen=True)
class TierMetrics:
    """单层(hich/medium/skip)的核心指标"""
    tier: str
    total_count: int = 0
    correct_count: int = 0
    hit_rate: float = 0.0
    roi: float = 0.0
    total_profit: float = 0.0
    total_stakes: float = 0.0
    max_drawdown: float = 0.0
    avg_odds: float = 0.0
    breakeven_hit_rate: float = 0.0  # 加权平均盈亏平衡点
    odds_buckets: Tuple[OddsBucketStats, ...] = ()

    # 方向细分
    home_count: int = 0
    home_correct: int = 0
    home_roi: float = 0.0
    draw_count: int = 0
    draw_correct: int = 0
    draw_roi: float = 0.0
    away_count: int = 0
    away_correct: int = 0
    away_roi: float = 0.0


@dataclass(frozen=True)
class ValidationMetrics:
    """一套方案的完整验证指标"""
    label: str
    params: StrategyParams
    total_matches: int = 0
    high: TierMetrics = field(default_factory=lambda: TierMetrics(tier=TIER_HIGH))
    medium: TierMetrics = field(default_factory=lambda: TierMetrics(tier=TIER_MEDIUM))
    skip_count: int = 0
    combined_roi: float = 0.0
    combined_profit: float = 0.0
    combined_stakes: float = 0.0
    coverage_rate: float = 0.0  # (high+medium) / total
    timestamp: str = ""


@dataclass(frozen=True)
class MetricDelta:
    """指标变化量"""
    name: str
    baseline_val: float
    optim_val: float
    delta: float
    delta_pct: float  # 变化百分比
    passed: bool  # 是否满足成功标准


@dataclass(frozen=True)
class ValidationResult:
    """单个方案的验证结论"""
    plan_label: str
    deltas: Tuple[MetricDelta, ...] = ()
    all_passed: bool = False
    verdict: str = "FAIL"
    verdict_reason: str = ""


# ────────────────────────────
# 赔率区间定义
# ────────────────────────────

ODDS_BUCKETS = [
    (1.01, 1.50, "极低赔率"),
    (1.50, 1.80, "低赔率"),
    (1.80, 2.20, "中赔率"),
    (2.20, 3.00, "中高赔率"),
    (3.00, 5.00, "高赔率"),
    (5.00, 100.0, "极高赔率"),
]


def _bucket_index(odds: float) -> int:
    """返回赔率所属区间索引"""
    for i, (lo, hi, _) in enumerate(ODDS_BUCKETS):
        if lo <= odds < hi:
            return i
    return len(ODDS_BUCKETS) - 1


def _breakeven_rate(avg_odds: float) -> float:
    """给定平均赔率，计算盈亏平衡命中率"""
    if avg_odds <= 1.0:
        return 1.0
    return 1.0 / avg_odds


# ────────────────────────────
# 回测引擎
# ────────────────────────────

def _load_validation_data(limit: int = 0) -> List[Dict]:
    """加载回测数据（同 param_optimizer，独立加载以保证隔离）"""
    from database.models import SessionLocal, Match, MatchStatus, Prediction
    # from bet_nn import BetNetPredictor, extract_features

    predictor = BetNetPredictor()
    if not predictor.is_ready():
        logger.warning("[validation] BetNet 未就绪")
        return []

    session = SessionLocal()
    try:
        q = session.query(Match).filter(
            Match.status == MatchStatus.FINISHED,
            Match.actual_outcome.isnot(None),
            Match.closing_odds_home.isnot(None),
            Match.closing_odds_home > 1.01,
        )
        if limit > 0:
            q = q.limit(limit)

        matches = q.all()
        if len(matches) < 50:
            return []

        match_ids = [m.id for m in matches]
        preds = session.query(Prediction).filter(
            Prediction.match_id.in_(match_ids),
            Prediction.play_type == "SPF",
        ).all()
        pred_map = {}
        for p in preds:
            probs = p.probabilities if isinstance(p.probabilities, dict) else json.loads(p.probabilities)
            pred_map[p.match_id] = probs

        data = []
        for match in matches:
            spf = pred_map.get(match.id)
            if not spf:
                continue

            odds = {
                "home": match.closing_odds_home,
                "draw": match.closing_odds_draw,
                "away": match.closing_odds_away,
            }

            odds_movement = {}
            for sel in ("home", "draw", "away"):
                closing = getattr(match, f"closing_odds_{sel}", None) or 0
                opening = getattr(match, f"opening_odds_{sel}", None) or 0
                odds_movement[sel] = (closing - opening) / opening if closing and opening else 0.0

            elo_diff = 0.0
            if match.home_team and match.away_team:
                elo_diff = (match.home_team.elo or 1500) - (match.away_team.elo or 1500)

            score_pred = session.query(Prediction).filter(
                Prediction.match_id == match.id,
                Prediction.play_type == "SCORE",
            ).first()
            score_probs = {}
            if score_pred and score_pred.probabilities:
                score_probs = (
                    score_pred.probabilities
                    if isinstance(score_pred.probabilities, dict)
                    else json.loads(score_pred.probabilities)
                )

            raw_feats = extract_features(
                spf_probs=spf, rq_probs=spf, score_top3=score_probs or spf,
                odds=odds, elo_diff=elo_diff, odds_movement=odds_movement,
                competition=match.competition or "",
            )
            nn_values = predictor.predict(raw_feats)

            is_jingcai = match.competition in ("EPL", "Bundesliga", "LaLiga", "SerieA")

            data.append({
                "match_id": match.id,
                "spf_probs": spf,
                "nn_values": nn_values,
                "odds": odds,
                "is_jingcai": is_jingcai,
                "actual_outcome": match.actual_outcome,
                "actual_home_goals": match.actual_home_goals,
                "actual_away_goals": match.actual_away_goals,
            })

        logger.info(f"[validation] 加载 {len(data)} 场数据")
        return data

    finally:
        session.close()


def run_backtest(params: StrategyParams, data: List[Dict], label: str = "") -> ValidationMetrics:
    """
    对单套参数运行完整回测，输出 ValidationMetrics。

    与 param_optimizer._backtest_params 的区别:
    - 额外记录赔率区间细分、方向细分、最大回撤
    - 输出结构化 ValidationMetrics 而非精简 OptimResult
    """
    tier_accum = {
        TIER_HIGH: _new_accum(),
        TIER_MEDIUM: _new_accum(),
    }

    for d in data:
        tier, _, _, edge_val, _ = classify_tier(
            d["spf_probs"], d["nn_values"], d["odds"],
            d["is_jingcai"], params,
        )

        if tier == TIER_SKIP:
            continue

        predicted, _, _ = select_spf_recommendation(
            d["spf_probs"], d["nn_values"], d["odds"], tier, params,
        )
        pred_odds = d["odds"].get(predicted, 2.0)
        pos = compute_position_ratio(tier, predicted, pred_odds, params)

        if pos <= 0:
            continue

        actual = d["actual_outcome"]
        is_correct = predicted == actual
        profit = (pred_odds - 1.0) * pos if is_correct else -pos

        acc = tier_accum[tier]
        acc["count"] += 1
        acc["stakes"] += pos
        acc["profit"] += profit
        if is_correct:
            acc["correct"] += 1

        # 赔率区间统计
        bi = _bucket_index(pred_odds)
        acc["bucket_counts"][bi] += 1
        acc["bucket_corrects"][bi] += int(is_correct)
        acc["bucket_stakes"][bi] += pos
        acc["bucket_profits"][bi] += profit
        acc["bucket_odds_sums"][bi] += pred_odds

        # 方向统计
        direction = predicted
        acc["dir_counts"][direction] += 1
        acc["dir_corrects"][direction] += int(is_correct)
        acc["dir_profits"][direction] += profit
        acc["dir_stakes"][direction] += pos

        # 回撤: 跟踪累计利润峰值
        acc["cum_profit"].append(acc["profit"])
        if acc["profit"] > acc["peak_profit"]:
            acc["peak_profit"] = acc["profit"]
        drawdown = acc["peak_profit"] - acc["profit"]
        if drawdown > acc["max_drawdown"]:
            acc["max_drawdown"] = drawdown

    # 组装 TierMetrics
    high_m = _build_tier_metrics(TIER_HIGH, tier_accum[TIER_HIGH])
    medium_m = _build_tier_metrics(TIER_MEDIUM, tier_accum[TIER_MEDIUM])
    skip_count = len(data) - high_m.total_count - medium_m.total_count

    combined_profit = high_m.total_profit + medium_m.total_profit
    combined_stakes = high_m.total_stakes + medium_m.total_stakes
    combined_roi = combined_profit / combined_stakes if combined_stakes > 0 else 0.0
    coverage = (high_m.total_count + medium_m.total_count) / len(data) if data else 0.0

    return ValidationMetrics(
        label=label or f"params_v{params.version}",
        params=params,
        total_matches=len(data),
        high=high_m,
        medium=medium_m,
        skip_count=skip_count,
        combined_roi=round(combined_roi, 4),
        combined_profit=round(combined_profit, 2),
        combined_stakes=round(combined_stakes, 2),
        coverage_rate=round(coverage, 4),
        timestamp=datetime.now(timezone.utc).isoformat(),
    )


def _new_accum() -> Dict:
    """创建层累加器"""
    n_buckets = len(ODDS_BUCKETS)
    return {
        "count": 0,
        "correct": 0,
        "stakes": 0.0,
        "profit": 0.0,
        "max_drawdown": 0.0,
        "peak_profit": 0.0,
        "cum_profit": [],
        "bucket_counts": [0] * n_buckets,
        "bucket_corrects": [0] * n_buckets,
        "bucket_stakes": [0.0] * n_buckets,
        "bucket_profits": [0.0] * n_buckets,
        "bucket_odds_sums": [0.0] * n_buckets,
        "dir_counts": {"home": 0, "draw": 0, "away": 0},
        "dir_corrects": {"home": 0, "draw": 0, "away": 0},
        "dir_profits": {"home": 0.0, "draw": 0.0, "away": 0.0},
        "dir_stakes": {"home": 0.0, "draw": 0.0, "away": 0.0},
    }


def _build_tier_metrics(tier: str, acc: Dict) -> TierMetrics:
    """从累加器构建 TierMetrics"""
    count = acc["count"]
    correct = acc["correct"]
    stakes = acc["stakes"]
    profit = acc["profit"]
    hit_rate = correct / count if count > 0 else 0.0
    roi = profit / stakes if stakes > 0 else 0.0
    avg_odds = stakes / count if count > 0 else 0.0
    be_rate = _breakeven_rate(avg_odds) if avg_odds > 1.0 else 0.0

    # 赔率区间
    buckets = []
    for i, (lo, hi, label) in enumerate(ODDS_BUCKETS):
        bc = acc["bucket_counts"][i]
        if bc == 0:
            buckets.append(OddsBucketStats(odds_min=lo, odds_max=hi))
            continue
        b_correct = acc["bucket_corrects"][i]
        b_stakes = acc["bucket_stakes"][i]
        b_profit = acc["bucket_profits"][i]
        b_avg_odds = acc["bucket_odds_sums"][i] / bc
        buckets.append(OddsBucketStats(
            odds_min=lo, odds_max=hi,
            count=bc, correct=b_correct,
            hit_rate=round(b_correct / bc, 4),
            roi=round(b_profit / b_stakes, 4) if b_stakes > 0 else 0.0,
            profit=round(b_profit, 2),
            stakes=round(b_stakes, 2),
            breakeven_rate=round(_breakeven_rate(b_avg_odds), 4),
        ))

    # 方向
    dir_stats = {}
    for d in ("home", "draw", "away"):
        dc = acc["dir_counts"][d]
        ds = acc["dir_stakes"][d]
        dir_stats[d] = {
            "count": dc,
            "correct": acc["dir_corrects"][d],
            "roi": round(acc["dir_profits"][d] / ds, 4) if ds > 0 else 0.0,
        }

    return TierMetrics(
        tier=tier,
        total_count=count,
        correct_count=correct,
        hit_rate=round(hit_rate, 4),
        roi=round(roi, 4),
        total_profit=round(profit, 2),
        total_stakes=round(stakes, 2),
        max_drawdown=round(acc["max_drawdown"], 2),
        avg_odds=round(avg_odds, 4),
        breakeven_hit_rate=round(be_rate, 4),
        odds_buckets=tuple(buckets),
        home_count=dir_stats["home"]["count"],
        home_correct=dir_stats["home"]["correct"],
        home_roi=dir_stats["home"]["roi"],
        draw_count=dir_stats["draw"]["count"],
        draw_correct=dir_stats["draw"]["correct"],
        draw_roi=dir_stats["draw"]["roi"],
        away_count=dir_stats["away"]["count"],
        away_correct=dir_stats["away"]["correct"],
        away_roi=dir_stats["away"]["roi"],
    )


# ────────────────────────────
# 对比引擎
# ────────────────────────────

# 每个方案的重点监控指标及成功标准
PLAN_CRITERIA = {
    "A": {
        "description": "赔率过滤优化",
        "core_metrics": [
            ("medium_roi", "medium层ROI转正", lambda v: v >= 0.0),
            ("combined_roi", "总ROI不低于基线", lambda v, b: v >= b),
            ("high_roi", "high层ROI不下降超过1个百分点", lambda v, b: v >= b - 0.01),
        ],
        "guardrails": [
            ("high_count", "high层场次数不减少超过20%", lambda v, b: v >= b * 0.8),
            ("coverage_rate", "覆盖率不低于15%", lambda v: v >= 0.15),
        ],
    },
    "B": {
        "description": "仓位调整优化",
        "core_metrics": [
            ("medium_roi", "medium层ROI改善至少0.5个百分点", lambda v, b: v >= b + 0.005),
            ("combined_roi", "总ROI不低于基线", lambda v, b: v >= b),
            ("max_drawdown_medium", "medium最大回撤不超过基线的1.5倍", lambda v, b: v <= b * 1.5 if b > 0 else v <= 200),
        ],
        "guardrails": [
            ("high_count", "high层场次数不减少超过10%", lambda v, b: v >= b * 0.9),
            ("coverage_rate", "覆盖率不低于15%", lambda v: v >= 0.15),
        ],
    },
    "C": {
        "description": "信号二次过滤",
        "core_metrics": [
            ("medium_hit_rate", "medium层命中率提升至少5个百分点", lambda v, b: v >= b + 0.05),
            ("medium_roi", "medium层ROI转正或改善至少1个百分点", lambda v, b: v >= b + 0.01),
            ("high_roi", "high层ROI不下降超过1个百分点", lambda v, b: v >= b - 0.01),
        ],
        "guardrails": [
            ("high_count", "high层不受影响", lambda v, b: v == b),
            ("medium_count", "medium层至少保留500场", lambda v: v >= 500),
        ],
    },
    "combined": {
        "description": "组合方案",
        "core_metrics": [
            ("medium_roi", "medium层ROI转正", lambda v: v >= 0.0),
            ("combined_roi", "总ROI转正且高于基线", lambda v, b: v >= b and v >= 0.0),
            ("max_drawdown_combined", "组合最大回撤不超过基线的1.3倍", lambda v, b: v <= b * 1.3 if b > 0 else v <= 150),
        ],
        "guardrails": [
            ("high_count", "high层场次数不减少超过20%", lambda v, b: v >= b * 0.8),
            ("high_roi", "high层ROI不低于基线", lambda v, b: v >= b - 0.01),
            ("coverage_rate", "覆盖率不低于10%", lambda v: v >= 0.10),
        ],
    },
}


def compare_metrics(
    baseline: ValidationMetrics,
    optimized: ValidationMetrics,
    plan_key: str = "combined",
) -> ValidationResult:
    """
    逐指标对比基线 vs 优化方案，自动判定 PASS/FAIL。

    Args:
        baseline: 基线回测结果
        optimized: 优化方案回测结果
        plan_key: 方案标识 (A/B/C/combined)
    """
    criteria = PLAN_CRITERIA.get(plan_key, PLAN_CRITERIA["combined"])

    # 提取指标值
    b_vals = _extract_metric_values(baseline)
    o_vals = _extract_metric_values(optimized)

    deltas = []

    # 检查核心指标
    for spec in criteria["core_metrics"]:
        metric_name = spec[0]
        b_val = b_vals.get(metric_name, 0.0)
        o_val = o_vals.get(metric_name, 0.0)
        delta = o_val - b_val
        delta_pct = delta / abs(b_val) if abs(b_val) > 1e-9 else (1.0 if delta > 0 else 0.0)

        check_fn = spec[2]
        if check_fn.__code__.co_argcount >= 2:
            passed = check_fn(o_val, b_val)
        else:
            passed = check_fn(o_val)

        deltas.append(MetricDelta(
            name=metric_name,
            baseline_val=round(b_val, 4),
            optim_val=round(o_val, 4),
            delta=round(delta, 4),
            delta_pct=round(delta_pct, 4),
            passed=passed,
        ))

    # 检查护栏指标
    for spec in criteria["guardrails"]:
        metric_name = spec[0]
        b_val = b_vals.get(metric_name, 0.0)
        o_val = o_vals.get(metric_name, 0.0)
        delta = o_val - b_val
        delta_pct = delta / abs(b_val) if abs(b_val) > 1e-9 else 0.0

        check_fn = spec[2]
        if check_fn.__code__.co_argcount >= 2:
            passed = check_fn(o_val, b_val)
        else:
            passed = check_fn(o_val)

        deltas.append(MetricDelta(
            name=f"[护栏] {metric_name}",
            baseline_val=round(b_val, 4),
            optim_val=round(o_val, 4),
            delta=round(delta, 4),
            delta_pct=round(delta_pct, 4),
            passed=passed,
        ))

    all_passed = all(d.passed for d in deltas)
    failed_metrics = [d.name for d in deltas if not d.passed]

    verdict = "PASS" if all_passed else "FAIL"
    verdict_reason = (
        "所有核心指标和护栏指标均通过"
        if all_passed
        else f"未通过指标: {', '.join(failed_metrics)}"
    )

    return ValidationResult(
        plan_label=f"Plan {plan_key}: {criteria['description']}",
        deltas=tuple(deltas),
        all_passed=all_passed,
        verdict=verdict,
        verdict_reason=verdict_reason,
    )


def _extract_metric_values(m: ValidationMetrics) -> Dict[str, float]:
    """从 ValidationMetrics 提取扁平化的指标值"""
    return {
        "medium_roi": m.medium.roi,
        "medium_hit_rate": m.medium.hit_rate,
        "medium_count": float(m.medium.total_count),
        "high_roi": m.high.roi,
        "high_hit_rate": m.high.hit_rate,
        "high_count": float(m.high.total_count),
        "combined_roi": m.combined_roi,
        "coverage_rate": m.coverage_rate,
        "max_drawdown_medium": m.medium.max_drawdown,
        "max_drawdown_combined": max(m.high.max_drawdown, m.medium.max_drawdown),
        "medium_avg_odds": m.medium.avg_odds,
        "medium_breakeven": m.medium.breakeven_hit_rate,
    }


# ────────────────────────────
# 统计显著性检验
# ────────────────────────────

def binomial_test(
    hits: int,
    trials: int,
    null_prob: float,
) -> Dict:
    """
    二项检验: 实际命中率是否显著高于零假设概率。

    用途: 验证优化后的命中率提升不是偶然。
    零假设: 实际命中率 = 基线命中率
    备择: 优化后命中率 > 基线命中率

    使用正态近似（样本量>30时足够精确）。
    """
    if trials == 0 or null_prob <= 0 or null_prob >= 1:
        return {"p_value": 1.0, "significant": False, "z_score": 0.0}

    observed_rate = hits / trials
    se = math.sqrt(null_prob * (1 - null_prob) / trials)
    if se < 1e-9:
        return {"p_value": 1.0, "significant": False, "z_score": 0.0}

    z = (observed_rate - null_prob) / se
    # 单侧检验 P(X > z)
    p_value = 1 - _normal_cdf(z)

    return {
        "p_value": round(p_value, 6),
        "significant": p_value < 0.05,
        "z_score": round(z, 4),
        "observed_rate": round(observed_rate, 4),
        "null_prob": round(null_prob, 4),
        "trials": trials,
    }


def _normal_cdf(x: float) -> float:
    """标准正态分布CDF（近似）"""
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def roi_confidence_interval(
    profit: float,
    stakes: float,
    n: int,
    confidence: float = 0.95,
) -> Dict:
    """
    ROI 置信区间估计。

    方法: 假设每场投注收益近似独立同分布，
    用样本标准差构建 t 分布置信区间。
    """
    if n < 2 or stakes <= 0:
        return {"roi_ci_low": 0.0, "roi_ci_high": 0.0, "reliable": False}

    roi = profit / stakes

    # 保守估计: 如果无法获得逐场数据，用二项分布近似
    # 每场收益方差 ≈ p*o^2 - (p*o - 1)^2 简化处理
    # 这里用 ROI 的保守区间: 用命中率构建
    hit_rate = (profit + stakes) / (stakes * 2) if stakes > 0 else 0.5
    se_hit = math.sqrt(hit_rate * (1 - hit_rate) / n) if n > 0 else 0

    # ROI 的标准误差 ≈ odds_avg * se_hit (保守放大)
    avg_odds = stakes / n if n > 0 else 2.0
    se_roi = avg_odds * se_hit

    # 95% 置信区间 ≈ ±1.96*SE
    z_val = 1.96
    ci_low = roi - z_val * se_roi
    ci_high = roi + z_val * se_roi

    return {
        "roi": round(roi, 4),
        "roi_ci_low": round(ci_low, 4),
        "roi_ci_high": round(ci_high, 4),
        "reliable": n >= 100,
        "sample_size": n,
    }


# ────────────────────────────
# 完整验证流程
# ────────────────────────────

def run_full_validation(
    plan_configs: Dict[str, StrategyParams],
    sample_limit: int = 0,
) -> Dict:
    """
    完整验证流程: 基线 → 逐方案回测 → 对比 → 统计检验 → 判定

    Args:
        plan_configs: {"A": params_A, "B": params_B, "C": params_C, "combined": params_combo}
        sample_limit: 数据限制（0=全部）

    Returns:
        完整验证报告字典
    """
    logger.info("[validation] 加载回测数据...")
    data = _load_validation_data(limit=sample_limit)
    if not data:
        logger.error("[validation] 无回测数据，验证终止")
        return {"error": "no backtest data"}

    # 1. 基线回测
    baseline_params = load_params()
    logger.info("[validation] 运行基线回测...")
    baseline = run_backtest(baseline_params, data, label="baseline")
    logger.info(
        f"[validation] 基线: high={baseline.high.total_count}场/{baseline.high.roi:.2%}, "
        f"medium={baseline.medium.total_count}场/{baseline.medium.roi:.2%}, "
        f"combined={baseline.combined_roi:.2%}"
    )

    # 2. 逐方案回测
    plan_results = {}
    for plan_key, params in plan_configs.items():
        logger.info(f"[validation] 回测方案 {plan_key}...")
        metrics = run_backtest(params, data, label=f"plan_{plan_key}")
        plan_results[plan_key] = metrics
        logger.info(
            f"[validation] 方案{plan_key}: high={metrics.high.total_count}场/{metrics.high.roi:.2%}, "
            f"medium={metrics.medium.total_count}场/{metrics.medium.roi:.2%}, "
            f"combined={metrics.combined_roi:.2%}"
        )

    # 3. 对比 + 判定
    comparisons = {}
    for plan_key, metrics in plan_results.items():
        result = compare_metrics(baseline, metrics, plan_key=plan_key)
        comparisons[plan_key] = result
        status = "✅ PASS" if result.all_passed else "❌ FAIL"
        logger.info(f"[validation] 方案{plan_key}: {status} — {result.verdict_reason}")

    # 4. 统计显著性检验
    significance = {}
    for plan_key, metrics in plan_results.items():
        sig = {}
        # medium 层命中率检验
        if metrics.medium.total_count > 0:
            sig["medium_hit_rate"] = binomial_test(
                metrics.medium.correct_count,
                metrics.medium.total_count,
                baseline.medium.hit_rate,
            )
        # medium 层 ROI 置信区间
        sig["medium_roi_ci"] = roi_confidence_interval(
            metrics.medium.total_profit,
            metrics.medium.total_stakes,
            metrics.medium.total_count,
        )
        # high 层 ROI 置信区间
        sig["high_roi_ci"] = roi_confidence_interval(
            metrics.high.total_profit,
            metrics.high.total_stakes,
            metrics.high.total_count,
        )
        significance[plan_key] = sig

    # 5. 组装报告
    report = {
        "validated_at": datetime.now(timezone.utc).isoformat(),
        "sample_size": len(data),
        "baseline": _metrics_to_dict(baseline),
        "plans": {},
    }

    for plan_key in plan_configs:
        m = plan_results[plan_key]
        c = comparisons[plan_key]
        s = significance.get(plan_key, {})

        report["plans"][plan_key] = {
            "metrics": _metrics_to_dict(m),
            "comparison": {
                "verdict": c.verdict,
                "verdict_reason": c.verdict_reason,
                "all_passed": c.all_passed,
                "deltas": [
                    {
                        "metric": d.name,
                        "baseline": d.baseline_val,
                        "optimized": d.optim_val,
                        "delta": d.delta,
                        "delta_pct": f"{d.delta_pct:.2%}",
                        "passed": d.passed,
                    }
                    for d in c.deltas
                ],
            },
            "significance": s,
        }

    # 保存报告
    report_path = os.path.join(VALIDATION_DIR, "validation_report.json")
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    logger.info(f"[validation] 报告已保存: {report_path}")

    return report


# ────────────────────────────
# 报告输出
# ────────────────────────────

def _metrics_to_dict(m: ValidationMetrics) -> Dict:
    """ValidationMetrics → 可序列化字典"""
    return {
        "label": m.label,
        "total_matches": m.total_matches,
        "skip_count": m.skip_count,
        "coverage_rate": f"{m.coverage_rate:.2%}",
        "combined": {
            "roi": f"{m.combined_roi:.2%}",
            "profit": m.combined_profit,
            "stakes": m.combined_stakes,
        },
        "high": _tier_to_dict(m.high),
        "medium": _tier_to_dict(m.medium),
    }


def _tier_to_dict(t: TierMetrics) -> Dict:
    """TierMetrics → 可序列化字典"""
    return {
        "count": t.total_count,
        "correct": t.correct_count,
        "hit_rate": f"{t.hit_rate:.2%}",
        "roi": f"{t.roi:.2%}",
        "profit": t.total_profit,
        "stakes": t.total_stakes,
        "avg_odds": round(t.avg_odds, 2),
        "breakeven_hit_rate": f"{t.breakeven_hit_rate:.2%}",
        "max_drawdown": t.max_drawdown,
        "odds_buckets": [
            {
                "range": f"{b.odds_min:.2f}-{b.odds_max:.2f}",
                "count": b.count,
                "hit_rate": f"{b.hit_rate:.2%}",
                "roi": f"{b.roi:.2%}",
                "breakeven": f"{b.breakeven_rate:.2%}",
                "profit": b.profit,
            }
            for b in t.odds_buckets
        ],
        "directions": {
            "home": {"count": t.home_count, "correct": t.home_correct, "roi": f"{t.home_roi:.2%}"},
            "draw": {"count": t.draw_count, "correct": t.draw_correct, "roi": f"{t.draw_roi:.2%}"},
            "away": {"count": t.away_count, "correct": t.away_correct, "roi": f"{t.away_roi:.2%}"},
        },
    }


def print_validation_summary(report: Dict) -> str:
    """格式化输出验证摘要"""
    lines = []
    lines.append("=" * 60)
    lines.append("优化方案验证报告")
    lines.append("=" * 60)

    b = report["baseline"]
    lines.append(f"\n样本量: {report['sample_size']} 场")
    lines.append(f"基线: combined ROI={b['combined']['roi']}, "
                 f"high={b['high']['count']}场/{b['high']['roi']}, "
                 f"medium={b['medium']['count']}场/{b['medium']['roi']}")

    for plan_key, plan_data in report.get("plans", {}).items():
        comp = plan_data["comparison"]
        status = "✅ PASS" if comp["all_passed"] else "❌ FAIL"
        m = plan_data["metrics"]

        lines.append(f"\n{'─' * 50}")
        lines.append(f"方案 {plan_key}: {status}")
        lines.append(f"  判定: {comp['verdict_reason']}")
        lines.append(f"  combined ROI={m['combined']['roi']}, "
                     f"high={m['high']['count']}场/{m['high']['roi']}, "
                     f"medium={m['medium']['count']}场/{m['medium']['roi']}")

        # 指标变化明细
        lines.append("  指标变化:")
        for d in comp["deltas"]:
            icon = "✓" if d["passed"] else "✗"
            lines.append(
                f"    {icon} {d['metric']}: "
                f"{d['baseline']:.4f} → {d['optimized']:.4f} "
                f"(Δ={d['delta']:+.4f})"
            )

        # 统计显著性
        sig = plan_data.get("significance", {})
        if "medium_hit_rate" in sig:
            ht = sig["medium_hit_rate"]
            lines.append(
                f"  命中率显著性: z={ht['z_score']:.2f}, "
                f"p={ht['p_value']:.4f}, "
                f"{'显著' if ht['significant'] else '不显著'}"
            )
        if "medium_roi_ci" in sig:
            ci = sig["medium_roi_ci"]
            lines.append(
                f"  medium ROI 95%CI: [{ci['roi_ci_low']:.2%}, {ci['roi_ci_high']:.2%}]"
            )

    return "\n".join(lines)
