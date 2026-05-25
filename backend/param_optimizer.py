"""
参数自动寻优 — 遍历不同参数组合，回测找出最优策略

设计:
1. 给定参数搜索空间，批量跑不同参数组合
2. 对每套参数，用历史数据回测，计算 ROI / 命中率 / 最大回撤
3. 输出最优参数集，保存到 strategy_config
4. 预留未来接入贝叶斯优化/进化算法

当前为骨架实现，后期可替换搜索策略。
"""
import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from itertools import product
from typing import Dict, List, Optional, Tuple

from strategy_config import StrategyParams, save_params, load_params
from tiered_strategy import (
    TIER_HIGH, TIER_MEDIUM, TIER_SKIP,
    classify_tier, analyze_match,
)
from utils.logger import get_logger

logger = get_logger("param_optimizer")

OPTIMIZER_DIR = "./data/strategy/optimizer"
os.makedirs(OPTIMIZER_DIR, exist_ok=True)


@dataclass(frozen=True)
class OptimResult:
    """单次回测结果"""
    params: StrategyParams
    total_matches: int
    high_count: int
    medium_count: int
    skip_count: int
    high_correct: int
    medium_correct: int
    high_roi: float
    medium_roi: float
    combined_roi: float
    max_drawdown: float


@dataclass
class OptimSearchSpace:
    """参数搜索空间定义"""
    nn_high_threshold: List[float] = field(default_factory=lambda: [0.38, 0.40, 0.42, 0.44])
    nn_medium_threshold: List[float] = field(default_factory=lambda: [0.35, 0.37, 0.39])
    draw_odds_min: List[float] = field(default_factory=lambda: [2.6, 2.8, 3.0])
    draw_odds_max: List[float] = field(default_factory=lambda: [3.3, 3.5, 3.7])
    high_value_min_model_conf: List[float] = field(default_factory=lambda: [0.38, 0.40, 0.42])
    high_value_min_edge: List[float] = field(default_factory=lambda: [0.01, 0.02, 0.03])
    min_top_confidence: List[float] = field(default_factory=lambda: [0.25, 0.30, 0.35])


def run_grid_search(
    search_space: Optional[OptimSearchSpace] = None,
    max_combinations: int = 500,
    sample_limit: int = 0,
) -> List[OptimResult]:
    """
    网格搜索：遍历参数组合，回测找最优。

    Args:
        search_space: 搜索空间，None 使用默认
        max_combinations: 最大组合数（防止组合爆炸）
        sample_limit: 回测样本限制（0=全部）
    """
    if search_space is None:
        search_space = OptimSearchSpace()

    # 构建参数组合
    keys = [
        "nn_high_threshold", "nn_medium_threshold", "draw_odds_min",
        "draw_odds_max", "high_value_min_model_conf", "high_value_min_edge",
        "min_top_confidence",
    ]
    value_lists = [getattr(search_space, k) for k in keys]
    combinations = list(product(*value_lists))

    if len(combinations) > max_combinations:
        logger.info(f"[optimizer] 组合数 {len(combinations)} 超过限制，采样 {max_combinations}")
        import random
        random.shuffle(combinations)
        combinations = combinations[:max_combinations]

    logger.info(f"[optimizer] 开始网格搜索: {len(combinations)} 组参数")

    # 加载回测数据
    backtest_data = _load_backtest_data(limit=sample_limit)
    if not backtest_data:
        logger.warning("[optimizer] 无回测数据")
        return []

    results: List[OptimResult] = []

    for i, combo in enumerate(combinations):
        overrides = dict(zip(keys, combo))
        base = load_params()
        data = {k: getattr(base, k) for k in StrategyParams.__dataclass_fields__}
        data.update(overrides)
        params = StrategyParams(**data)

        result = _backtest_params(params, backtest_data)
        results.append(result)

        if (i + 1) % 50 == 0:
            logger.info(f"[optimizer] 进度 {i+1}/{len(combinations)}, best_roi={max(r.combined_roi for r in results):.2%}")

    # 按综合 ROI 排序
    results.sort(key=lambda r: r.combined_roi, reverse=True)

    # 保存最优参数
    if results and results[0].combined_roi > 0:
        best = results[0]
        save_params(best.params)
        logger.info(
            f"[optimizer] 最优参数: ROI={best.combined_roi:.2%}, "
            f"high={best.high_count}场({best.high_roi:.2%}), "
            f"medium={best.medium_count}场({best.medium_roi:.2%})"
        )

    _save_search_results(results)
    return results


def _load_backtest_data(limit: int = 0) -> List[Dict]:
    """从数据库加载回测数据（已结束比赛 + 预测 + 赔率 + 结果）"""
    import json
    from database.models import SessionLocal, Match, MatchStatus, Prediction
    from bet_nn import BetNetPredictor, extract_features

    predictor = BetNetPredictor()
    if not predictor.is_ready():
        logger.warning("[optimizer] BetNet 未就绪")
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

        # 批量获取预测
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
                score_probs = score_pred.probabilities if isinstance(score_pred.probabilities, dict) else json.loads(score_pred.probabilities)

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

        logger.info(f"[optimizer] 加载 {len(data)} 场回测数据")
        return data

    finally:
        session.close()


def _backtest_params(params: StrategyParams, data: List[Dict]) -> OptimResult:
    """对单套参数运行回测（含平局信号+低赔率降权+自适应仓位）"""
    from tiered_strategy import select_spf_recommendation
    from strategy_config import compute_position_ratio

    high_count = 0
    medium_count = 0
    skip_count = 0
    high_correct = 0
    medium_correct = 0
    high_profit = 0.0
    medium_profit = 0.0
    high_stakes = 0.0
    medium_stakes = 0.0

    for d in data:
        tier, _, _, edge_val, _ = classify_tier(
            d["spf_probs"], d["nn_values"], d["odds"],
            d["is_jingcai"], params,
        )

        actual = d["actual_outcome"]
        odds = d["odds"]

        if tier == TIER_SKIP:
            skip_count += 1
            continue

        # 使用优化后的推荐选择（含平局信号+低赔率主胜降权）
        predicted, _, _ = select_spf_recommendation(
            d["spf_probs"], d["nn_values"], odds, tier, params,
        )
        pred_odds = odds.get(predicted, 2.0)

        # 自适应仓位
        pos = compute_position_ratio(tier, predicted, pred_odds, params)

        if pos <= 0:
            # 仓位为0 → 实际等于skip
            skip_count += 1
            continue

        if tier == TIER_HIGH:
            high_count += 1
            high_stakes += pos
            if predicted == actual:
                high_correct += 1
                high_profit += (pred_odds - 1.0) * pos
            else:
                high_profit -= pos
        elif tier == TIER_MEDIUM:
            medium_count += 1
            medium_stakes += pos
            if predicted == actual:
                medium_correct += 1
                medium_profit += (pred_odds - 1.0) * pos
            else:
                medium_profit -= pos

        else:
            skip_count += 1

    total = len(data)
    high_roi = high_profit / high_stakes if high_stakes > 0 else 0.0
    medium_roi = medium_profit / medium_stakes if medium_stakes > 0 else 0.0
    combined_profit = high_profit + medium_profit
    combined_stakes = high_stakes + medium_stakes
    combined_roi = combined_profit / combined_stakes if combined_stakes > 0 else 0.0

    return OptimResult(
        params=params,
        total_matches=total,
        high_count=high_count,
        medium_count=medium_count,
        skip_count=skip_count,
        high_correct=high_correct,
        medium_correct=medium_correct,
        high_roi=round(high_roi, 4),
        medium_roi=round(medium_roi, 4),
        combined_roi=round(combined_roi, 4),
        max_drawdown=0.0,  # 后续可加入回撤计算
    )


def _save_search_results(results: List[OptimResult], top_n: int = 20) -> None:
    """保存搜索结果"""
    output = []
    for r in results[:top_n]:
        output.append({
            "high_roi": r.high_roi,
            "medium_roi": r.medium_roi,
            "combined_roi": r.combined_roi,
            "high_count": r.high_count,
            "medium_count": r.medium_count,
            "skip_count": r.skip_count,
            "params": {
                k: getattr(r.params, k)
                for k in StrategyParams.__dataclass_fields__
            },
        })

    path = os.path.join(OPTIMIZER_DIR, "search_results.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({
            "searched_at": datetime.now(timezone.utc).isoformat(),
            "total_combinations": len(results),
            "top_results": output,
        }, f, indent=2, ensure_ascii=False)


# ────────────────────────────
# 调度器入口
# ────────────────────────────

def param_optimize_job() -> None:
    """调度器定时任务：每周参数寻优"""
    results = run_grid_search(max_combinations=200, sample_limit=5000)
    if results:
        best = results[0]
        logger.info(
            f"[optimizer] 寻优完成: best_roi={best.combined_roi:.2%}, "
            f"high={best.high_count}, medium={best.medium_count}"
        )
