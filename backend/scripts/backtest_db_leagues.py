"""
数据库联赛 walk-forward 回测

从数据库读取已结束的5大联赛比赛，用 walk-forward 方式计算
Elo 和球队统计，验证/优化融合权重。

与 backtest_leagues.py 的区别：
- 数据源: 本地数据库（而非在线下载 CSV）
- 样本量: ~5000+ 场（3赛季5联赛），openfootball 导入后可达 ~20000+
- 支持半场比分校准
- 支持 Dixon-Coles rho / DRAW_INFLATION 参数校准
- 支持 WeightLearner 联动

用法:
    cd backend && python backtest_db_leagues.py
    cd backend && python backtest_db_leagues.py --calibrate-dc
    cd backend && python backtest_db_leagues.py --learn-weights
    cd backend && python backtest_db_leagues.py --full
"""

from __future__ import annotations

import math
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Tuple, Optional

import numpy as np
from scipy.optimize import minimize_scalar, minimize
from scipy.stats import poisson

from core.prediction_engine import (
    PredictionEngine,
    MatchContext,
    TeamContext,
    PoissonModel,
    DEFAULT_WEIGHTS,
    DIXON_COLES_RHO,
    DRAW_INFLATION_FACTOR,
    MAX_GOALS,
    brier_score,
    direction_correct,
)


# ────────────────────────────
# 配置
# ────────────────────────────
LEAGUE_PREFIXES = ["EPL", "LALIGA", "BUNDESLIGA", "SERIEA", "LIGUE1"]
LEAGUE_EXACT = ["EPL", "LaLiga", "Bundesliga", "SerieA", "Ligue1"]
WALK_FORWARD_ELO_K = 20
MIN_ELO_MATCHES = 5  # 少于 N 场时用默认 Elo


@dataclass
class WalkForwardState:
    """Walk-forward 计算状态"""
    elos: Dict[int, int] = field(default_factory=dict)
    matches_played: Dict[int, int] = field(default_factory=dict)
    goals_scored: Dict[int, List[int]] = field(default_factory=lambda: defaultdict(list))
    goals_conceded: Dict[int, List[int]] = field(default_factory=lambda: defaultdict(list))
    ht_goals_scored: Dict[int, List[int]] = field(default_factory=lambda: defaultdict(list))
    ht_goals_conceded: Dict[int, List[int]] = field(default_factory=lambda: defaultdict(list))


def _is_league_competition(competition: str) -> bool:
    """检查是否是5大联赛（兼容 'EPL 2024-25' 和 'EPL' 两种格式）"""
    if not competition:
        return False
    # 精确匹配（如 "EPL", "Bundesliga"）
    if competition in LEAGUE_EXACT:
        return True
    # 前缀匹配（如 "EPL 2024-25"）
    for prefix in LEAGUE_PREFIXES:
        if competition.startswith(prefix):
            return True
    return False


def fetch_finished_league_matches(db) -> List:
    """从数据库获取所有已结束的5大联赛比赛，按时间排序"""
    from database.models import Match
    matches = (
        db.query(Match)
        .filter(Match.status == "finished")
        .filter(Match.actual_outcome.isnot(None))
        .filter(Match.actual_home_goals.isnot(None))
        .filter(Match.actual_away_goals.isnot(None))
        .order_by(Match.kickoff_at.asc())
        .all()
    )
    return [m for m in matches if _is_league_competition(m.competition or "")]


def walk_forward_elo(
    state: WalkForwardState,
    home_id: int,
    away_id: int,
    home_goals: int,
    away_goals: int,
) -> Tuple[int, int]:
    """
    更新 walk-forward Elo，返回赛前 Elo 值。
    每场比赛只用之前的数据计算，避免前瞻偏差。
    """
    home_elo = state.elos.get(home_id, 1500)
    away_elo = state.elos.get(away_id, 1500)

    # 联赛主场优势: +65 Elo (约等于 0.55 预期胜率)
    home_advantage = 65
    effective_home_elo = home_elo + home_advantage

    expected_home = 1 / (1 + 10 ** ((away_elo - effective_home_elo) / 400))

    if home_goals > away_goals:
        actual = 1.0
    elif home_goals == away_goals:
        actual = 0.5
    else:
        actual = 0.0

    delta = WALK_FORWARD_ELO_K * (actual - expected_home)
    state.elos[home_id] = int(home_elo + delta)
    state.elos[away_id] = int(away_elo - delta)
    state.matches_played[home_id] = state.matches_played.get(home_id, 0) + 1
    state.matches_played[away_id] = state.matches_played.get(away_id, 0) + 1

    return home_elo, away_elo


def walk_forward_stats(
    state: WalkForwardState,
    team_id: int,
    home_goals: int,
    away_goals: int,
    is_home: bool,
) -> Tuple[float, float]:
    """返回赛前场均进球/失球"""
    gs_list = state.goals_scored.get(team_id, [])
    gc_list = state.goals_conceded.get(team_id, [])

    avg_gs = sum(gs_list) / len(gs_list) if gs_list else 1.3
    avg_gc = sum(gc_list) / len(gc_list) if gc_list else 1.3

    # 赛后更新
    goals = home_goals if is_home else away_goals
    conceded = away_goals if is_home else home_goals
    state.goals_scored[team_id].append(goals)
    state.goals_conceded[team_id].append(conceded)

    return avg_gs, avg_gc


def walk_forward_ht_stats(
    state: WalkForwardState,
    team_id: int,
    ht_home: Optional[int],
    ht_away: Optional[int],
    is_home: bool,
) -> Tuple[float, float]:
    """返回赛前半场场均进球/失球"""
    ht_gs_list = state.ht_goals_scored.get(team_id, [])
    ht_gc_list = state.ht_goals_conceded.get(team_id, [])

    avg_ht_gs = sum(ht_gs_list) / len(ht_gs_list) if ht_gs_list else 0.6
    avg_ht_gc = sum(ht_gc_list) / len(ht_gc_list) if ht_gc_list else 0.6

    if ht_home is not None and ht_away is not None:
        goals = ht_home if is_home else ht_away
        conceded = ht_away if is_home else ht_home
        state.ht_goals_scored[team_id].append(goals)
        state.ht_goals_conceded[team_id].append(conceded)

    return avg_ht_gs, avg_ht_gc


def build_context_from_match(
    match,
    state: WalkForwardState,
) -> Optional[Tuple[MatchContext, str]]:
    """从 DB Match + walk-forward state 构建 MatchContext"""
    home = match.home_team
    away = match.away_team
    if not home or not away:
        return None

    home_elo, away_elo = walk_forward_elo(
        state, home.id, away.id,
        match.actual_home_goals, match.actual_away_goals,
    )

    home_gs, home_gc = walk_forward_stats(
        state, home.id, match.actual_home_goals, match.actual_away_goals, True,
    )
    away_gs, away_gc = walk_forward_stats(
        state, away.id, match.actual_home_goals, match.actual_away_goals, False,
    )

    # 半场统计
    ht_home = getattr(match, "ht_home_goals", None)
    ht_away = getattr(match, "ht_away_goals", None)
    home_ht_gs, home_ht_gc = walk_forward_ht_stats(
        state, home.id, ht_home, ht_away, True,
    )
    away_ht_gs, away_ht_gc = walk_forward_ht_stats(
        state, away.id, ht_home, ht_away, False,
    )

    # 赔率: 优先 closing_odds，其次 odds
    odds_h = match.closing_odds_home or match.odds_home
    odds_d = match.closing_odds_draw or match.odds_draw
    odds_a = match.closing_odds_away or match.odds_away

    has_closing = all([
        match.closing_odds_home and match.closing_odds_home > 1.01,
        match.closing_odds_draw and match.closing_odds_draw > 1.01,
        match.closing_odds_away and match.closing_odds_away > 1.01,
    ])

    home_ctx = TeamContext(
        team_id=home.id,
        name=home.name,
        elo=home_elo,
        fifa_rank=home.fifa_rank or 100,
        avg_goals_scored=home_gs,
        avg_goals_conceded=home_gc,
        form_factor=1.0,
        key_players_available=11,
        key_players_total=11,
        squad_fatigue_index=0.5,
        rest_days=7,
        tactical_style=home.tactical_style or "balanced",
        coach_rating=home.coach_rating or 0.5,
        home_away_factor=1.0,
        weather_adaptability=1.0,
        recent_results=home.recent_results or "",
    )

    away_ctx = TeamContext(
        team_id=away.id,
        name=away.name,
        elo=away_elo,
        fifa_rank=away.fifa_rank or 100,
        avg_goals_scored=away_gs,
        avg_goals_conceded=away_gc,
        form_factor=1.0,
        key_players_available=11,
        key_players_total=11,
        squad_fatigue_index=0.5,
        rest_days=7,
        tactical_style=away.tactical_style or "balanced",
        coach_rating=away.coach_rating or 0.5,
        home_away_factor=1.0,
        weather_adaptability=1.0,
        recent_results=away.recent_results or "",
    )

    ctx = MatchContext(
        match_id=match.id,
        home_team=home_ctx,
        away_team=away_ctx,
        stage="group",
        is_knockout=False,
        odds_home=odds_h or 2.5,
        odds_draw=odds_d or 3.2,
        odds_away=odds_a or 2.8,
        closing_odds_home=match.closing_odds_home,
        closing_odds_draw=match.closing_odds_draw,
        closing_odds_away=match.closing_odds_away,
        venue_type="home",
        weather="clear",
        temperature=20.0,
        pitch_condition="good",
        schedule_density="normal",
    )

    actual = match.actual_outcome
    return ctx, actual


# ────────────────────────────
# 核心回测
# ────────────────────────────

def run_backtest(
    matches: List,
    weights: Dict[str, float],
    label: str = "DB League Backtest",
) -> Dict[str, float]:
    """
    对数据库联赛比赛跑 walk-forward 回测。
    返回 {accuracy, brier, log_loss, n, high_conf_accuracy}。
    """
    state = WalkForwardState()
    engine = PredictionEngine(weights=weights)

    results = []
    by_league = defaultdict(list)

    for match in matches:
        built = build_context_from_match(match, state)
        if built is None:
            continue

        ctx, actual = built

        # 仅在有赔率时评估（无赔率的用默认值，准确性差）
        has_odds = (
            (match.closing_odds_home and match.closing_odds_home > 1.01)
            or (match.odds_home and match.odds_home > 1.01)
        )

        try:
            result = engine.predict(ctx)
            spf = result.spf

            correct = direction_correct(spf, actual)
            bs = sum(
                brier_score(spf[k], 1 if actual == k else 0)
                for k in ["home", "draw", "away"]
            ) / 3.0
            ll = -math.log(max(spf.get(actual, 1e-6), 1e-6))

            entry = {
                "correct": correct,
                "brier": bs,
                "log_loss": ll,
                "max_prob": max(spf.values()),
                "confidence": result.confidence,
                "has_odds": has_odds,
                "competition": match.competition,
            }
            results.append(entry)
            by_league[match.competition].append(entry)
        except Exception:
            continue

    if not results:
        print(f"  {label}: 无有效数据")
        return {}

    # 全局指标
    n = len(results)
    acc = sum(r["correct"] for r in results) / n
    brier = sum(r["brier"] for r in results) / n
    log_loss = sum(r["log_loss"] for r in results) / n
    high_conf = [r for r in results if r["confidence"] == "high"]
    hc_acc = (
        sum(r["correct"] for r in high_conf) / len(high_conf)
        if high_conf else 0
    )

    # 有赔率子集
    with_odds = [r for r in results if r["has_odds"]]
    odds_acc = sum(r["correct"] for r in with_odds) / len(with_odds) if with_odds else 0
    odds_brier = sum(r["brier"] for r in with_odds) / len(with_odds) if with_odds else 0

    print(f"\n  {label} ({n} 场)")
    print(f"    方向准确率: {acc*100:.1f}%")
    print(f"    Brier Score: {brier:.4f}")
    print(f"    Log Loss:    {log_loss:.4f}")
    print(f"    高置信准确率: {hc_acc*100:.1f}% ({len(high_conf)}/{n})")
    if with_odds:
        print(f"    有赔率子集: {odds_acc*100:.1f}% ({len(with_odds)} 场, Brier={odds_brier:.4f})")

    # 按联赛分解
    print(f"    {'联赛':<12} {'场次':<6} {'准确率':<8} {'Brier':<8}")
    print(f"    {'-'*40}")
    for comp in sorted(by_league.keys()):
        league_results = by_league[comp]
        ln = len(league_results)
        la = sum(r["correct"] for r in league_results) / ln
        lb = sum(r["brier"] for r in league_results) / ln
        print(f"    {comp:<12} {ln:<6} {la*100:<7.1f}% {lb:<8.4f}")

    return {
        "accuracy": acc,
        "brier": brier,
        "log_loss": log_loss,
        "n": n,
        "high_conf_accuracy": hc_acc,
        "with_odds_accuracy": odds_acc,
        "with_odds_brier": odds_brier,
        "with_odds_n": len(with_odds),
    }


# ────────────────────────────
# Dixon-Coles 参数校准
# ────────────────────────────

def _neg_log_likelihood_dc(
    rho: float,
    draw_inflation: float,
    matches_data: List[Tuple[float, float, int, int]],
) -> float:
    """
    计算给定 (rho, draw_inflation) 下的负对数似然。
    matches_data: [(lambda_h, lambda_a, home_goals, away_goals), ...]
    """
    nll = 0.0
    for lam_h, lam_a, hg, ag in matches_data:
        # 基础泊松
        base_h = poisson.pmf(hg, lam_h) if hg < MAX_GOALS else (1 - poisson.cdf(MAX_GOALS - 1, lam_h))
        base_a = poisson.pmf(ag, lam_a) if ag < MAX_GOALS else (1 - poisson.cdf(MAX_GOALS - 1, lam_a))

        # Dixon-Coles tau
        if hg == 0 and ag == 0:
            tau = 1.0 - lam_h * lam_a * rho
        elif hg == 1 and ag == 0:
            tau = 1.0 + lam_h * rho
        elif hg == 0 and ag == 1:
            tau = 1.0 + lam_a * rho
        elif hg == 1 and ag == 1:
            tau = 1.0 - rho
        else:
            tau = 1.0

        prob = max(tau * base_h * base_a, 1e-10)

        # 对平局应用 draw_inflation
        if hg == ag:
            prob *= draw_inflation

        nll -= math.log(prob)

    return nll


def calibrate_dixon_coles(matches: List) -> Tuple[float, float]:
    """
    用数据库联赛数据校准 DIXON_COLES_RHO 和 DRAW_INFLATION_FACTOR。
    返回 (最优_rho, 最优_draw_inflation)。
    """
    print("\n" + "=" * 60)
    print("  Dixon-Coles 参数校准 (MLE)")
    print("=" * 60)

    state = WalkForwardState()
    engine = PredictionEngine(weights=DEFAULT_WEIGHTS.copy())

    # 第一遍: 收集所有 (lambda_h, lambda_a, home_goals, away_goals)
    matches_data = []
    for match in matches:
        built = build_context_from_match(match, state)
        if built is None:
            continue
        ctx, _ = built
        try:
            lam_h, lam_a = PoissonModel._compute_lambdas(ctx)
            matches_data.append((lam_h, lam_a, match.actual_home_goals, match.actual_away_goals))
        except Exception:
            continue

    print(f"  有效样本: {len(matches_data)} 场")

    # 先单独优化 rho
    def neg_ll_rho(rho: float) -> float:
        return _neg_log_likelihood_dc(rho, DRAW_INFLATION_FACTOR, matches_data)

    res_rho = minimize_scalar(neg_ll_rho, bounds=(-0.2, 0.1), method="bounded")
    best_rho = res_rho.x

    # 再联合优化 (rho, draw_inflation)
    def neg_ll_joint(params: np.ndarray) -> float:
        rho, di = params[0], params[1]
        if di < 1.0 or di > 1.5:
            return 1e10
        return _neg_log_likelihood_dc(rho, di, matches_data)

    res_joint = minimize(
        neg_ll_joint,
        x0=np.array([best_rho, DRAW_INFLATION_FACTOR]),
        method="L-BFGS-B",
        bounds=[(-0.2, 0.1), (1.0, 1.5)],
    )
    opt_rho, opt_di = res_joint.x

    # 对比当前参数 vs 优化参数的似然
    current_nll = _neg_log_likelihood_dc(DIXON_COLES_RHO, DRAW_INFLATION_FACTOR, matches_data)
    optimized_nll = res_joint.fun

    print(f"\n  当前参数:  rho={DIXON_COLES_RHO:.4f}, draw_inflation={DRAW_INFLATION_FACTOR:.4f}")
    print(f"  优化参数:  rho={opt_rho:.4f}, draw_inflation={opt_di:.4f}")
    print(f"  当前 NLL:  {current_nll:.2f}")
    print(f"  优化 NLL:  {optimized_nll:.2f}")
    print(f"  改善:      {current_nll - optimized_nll:.2f} ({(current_nll - optimized_nll) / current_nll * 100:.2f}%)")

    # 验证: 计算实际平局率 vs 模型平局率
    actual_draws = sum(1 for _, _, hg, ag in matches_data if hg == ag)
    actual_draw_rate = actual_draws / len(matches_data)

    # 用当前参数和优化参数分别计算模型平局率
    model_draw_current = 0.0
    model_draw_optimized = 0.0
    for lam_h, lam_a, _, _ in matches_data:
        for i in range(MAX_GOALS + 1):
            p_h = poisson.pmf(i, lam_h)
            for j in range(MAX_GOALS + 1):
                p_a = poisson.pmf(j, lam_a)
                if i == j:
                    if i == 0:
                        tau_cur = 1.0 - lam_h * lam_a * DIXON_COLES_RHO
                        tau_opt = 1.0 - lam_h * lam_a * opt_rho
                    elif i == 1:
                        tau_cur = 1.0 - DIXON_COLES_RHO
                        tau_opt = 1.0 - opt_rho
                    else:
                        tau_cur = tau_opt = 1.0
                    model_draw_current += p_h * p_a * tau_cur * DRAW_INFLATION_FACTOR
                    model_draw_optimized += p_h * p_a * tau_opt * opt_di

    model_draw_current /= len(matches_data)
    model_draw_optimized /= len(matches_data)

    print(f"\n  实际平局率:   {actual_draw_rate*100:.1f}%")
    print(f"  模型平局率(当前): {model_draw_current*100:.1f}%")
    print(f"  模型平局率(优化): {model_draw_optimized*100:.1f}%")

    return opt_rho, opt_di


# ────────────────────────────
# 半场比分校准
# ────────────────────────────

def calibrate_half_time(matches: List) -> Dict[str, Any]:
    """
    用半场比分数据校准 HALF (半全场) 预测。
    计算 HT→FT 的实际转换概率矩阵。
    """
    print("\n" + "=" * 60)
    print("  半场比分校准 (HT→FT 转换矩阵)")
    print("=" * 60)

    # 收集半场→全场结果
    ht_ft_counts = defaultdict(lambda: defaultdict(int))
    total = 0
    no_ht = 0

    for match in matches:
        ht_h = getattr(match, "ht_home_goals", None)
        ht_a = getattr(match, "ht_away_goals", None)
        if ht_h is None or ht_a is None:
            no_ht += 1
            continue

        if ht_h > ht_a:
            ht_result = "home"
        elif ht_h == ht_a:
            ht_result = "draw"
        else:
            ht_result = "away"

        ft_result = match.actual_outcome  # home/draw/away
        ht_ft_counts[ht_result][ft_result] += 1
        total += 1

    print(f"  有半场比分: {total} 场")
    print(f"  无半场比分: {no_ht} 场")

    if total == 0:
        print("  ⚠️ 无半场比分数据，跳过校准")
        return {}

    # 计算转换概率矩阵
    ht_labels = ["home", "draw", "away"]
    ft_labels = ["home", "draw", "away"]
    half_labels_cn = {"home": "主", "draw": "平", "away": "客"}

    print(f"\n  HT\\FT       主胜      平局      客胜")
    print(f"  {'-'*45}")
    transition = {}
    for ht in ht_labels:
        row_total = sum(ht_ft_counts[ht].values())
        if row_total == 0:
            continue
        probs = {}
        line = f"  半场{half_labels_cn[ht]:<4}"
        for ft in ft_labels:
            p = ht_ft_counts[ht][ft] / row_total
            probs[ft] = p
            line += f"  {p*100:5.1f}%"
        print(line)
        transition[ht] = probs

    # 实际半场结果分布
    ht_dist = {}
    for ht in ht_labels:
        ht_dist[ht] = sum(ht_ft_counts[ht].values()) / total

    print(f"\n  半场结果分布: 主{ht_dist.get('home',0)*100:.1f}% "
          f"平{ht_dist.get('draw',0)*100:.1f}% "
          f"客{ht_dist.get('away',0)*100:.1f}%")

    # 生成 HALF 预测的校准映射
    half_mapping = {}
    for ht in ht_labels:
        for ft in ft_labels:
            ht_cn = half_labels_cn[ht]
            ft_cn = half_labels_cn[ft]
            key = f"{ht_cn}{ft_cn}"
            p_ht = ht_dist.get(ht, 1 / 3)
            p_ft_given_ht = transition.get(ht, {}).get(ft, 1 / 3)
            half_mapping[key] = p_ht * p_ft_given_ht

    # 归一化
    total_p = sum(half_mapping.values())
    half_mapping = {k: v / total_p for k, v in half_mapping.items()}

    print(f"\n  校准后半全场概率:")
    for k, v in sorted(half_mapping.items()):
        print(f"    {k}: {v*100:.2f}%")

    return {
        "transition": transition,
        "ht_distribution": ht_dist,
        "half_mapping": half_mapping,
        "total_samples": total,
    }


# ────────────────────────────
# 权重学习 (集成 WeightLearner)
# ────────────────────────────

def _precompute_submodels(
    matches: List,
) -> List[Dict[str, Any]]:
    """
    预计算每场比赛的4个子模型输出。
    返回 [{elo, poisson, players, market, actual, has_real_odds, competition}, ...]

    之后权重优化只需要做线性组合，不需要重跑完整预测引擎。
    速度提升 ~50x。
    """
    from core.prediction_engine import EloModel, PoissonModel, PlayerAdjustmentModel, MarketModel

    state = WalkForwardState()
    precomputed = []

    for match in matches:
        built = build_context_from_match(match, state)
        if built is None:
            continue
        ctx, actual = built

        elo_out = EloModel.predict(ctx)
        poisson_out = PoissonModel.predict(ctx)
        players_factor = PlayerAdjustmentModel.predict(ctx)
        market_out = MarketModel.predict(ctx)

        has_real_odds = (
            market_out is not None
            and (ctx.has_closing_odds or (ctx.odds_home and ctx.odds_home > 1.01))
        )

        precomputed.append({
            "elo": elo_out,
            "poisson": poisson_out["spf"],
            "players": players_factor,
            "market": market_out,
            "actual": actual,
            "has_real_odds": has_real_odds,
            "competition": match.competition,
        })

    return precomputed


def _fast_fuse_and_brier(
    precomputed: List[Dict[str, Any]],
    w: np.ndarray,
) -> float:
    """
    给定权重数组 [elo, poisson, players, market]，快速计算融合后 Brier Score。
    预计算子模型输出后，只需要线性组合 + Brier 计算。
    """
    w = np.asarray(w, dtype=float)
    total_w = float(w.sum())
    if total_w <= 0:
        return 1.0

    weights = {
        "elo": max(0.0, float(w[0]) / total_w),
        "poisson": max(0.0, float(w[1]) / total_w),
        "players": max(0.0, float(w[2]) / total_w),
        "market": max(0.0, float(w[3]) / total_w),
    }

    total_brier = 0.0
    n = 0

    for entry in precomputed:
        elo = entry["elo"]
        poisson = entry["poisson"]
        players = entry["players"]
        market = entry["market"]
        actual = entry["actual"]

        # players 修正强度
        adjust_strength = min(1.0, weights["players"] * 3.0)
        blend_factor = 1.0 + (players - 1.0) * adjust_strength

        # 应用 players 修正
        def _adjust(probs: Dict[str, float], factor: float) -> Dict[str, float]:
            home_adj = probs["home"] * factor
            away_adj = probs["away"] / factor if factor > 0 else probs["away"]
            draw_adj = probs["draw"]
            t = home_adj + draw_adj + away_adj
            return {"home": home_adj / t, "draw": draw_adj / t, "away": away_adj / t}

        elo_adj = _adjust(elo, blend_factor)
        poisson_adj = _adjust(poisson, blend_factor)

        # 线性融合
        fused = {}
        for outcome in ["home", "draw", "away"]:
            val = (
                weights["elo"] * elo_adj[outcome]
                + weights["poisson"] * poisson_adj[outcome]
                + weights["market"] * (market.get(outcome, 1 / 3.0) if market else 0)
            )
            fused[outcome] = val

        # 归一化
        t = sum(fused.values())
        if t <= 0:
            continue
        fused = {k: max(0.001, v / t) for k, v in fused.items()}

        # Brier Score
        bs = sum(
            (fused[k] - (1 if actual == k else 0)) ** 2
            for k in ["home", "draw", "away"]
        ) / 3.0
        total_brier += bs
        n += 1

    return total_brier / n if n > 0 else 1.0


def learn_weights_with_wf_data(db, matches: List) -> Dict[str, Any]:
    """
    用 walk-forward 数据学习融合权重。
    预计算子模型输出后快速优化，避免重复跑完整预测引擎。
    """
    print("\n" + "=" * 60)
    print("  融合权重学习 (Walk-Forward, 预计算加速)")
    print("=" * 60)

    # Step 1: 预计算子模型输出（只跑一次完整预测引擎）
    print("  预计算子模型输出...")
    precomputed = _precompute_submodels(matches)
    print(f"  有效样本: {len(precomputed)} 场")

    if len(precomputed) < 100:
        print("  ⚠️ 样本不足，跳过权重学习")
        return {}

    # Step 2: 默认权重 Brier
    from weight_learner import _dict_to_weights, _weights_to_dict, _get_weight_bounds
    x0 = _dict_to_weights(DEFAULT_WEIGHTS)
    bounds = _get_weight_bounds()

    default_brier = _fast_fuse_and_brier(precomputed, x0)
    print(f"  默认权重 Brier: {default_brier:.4f}")

    # Step 3: 全局优化
    result = minimize(
        fun=_fast_fuse_and_brier,
        x0=x0,
        args=(precomputed,),
        method="L-BFGS-B",
        bounds=bounds,
        options={"maxiter": 500, "disp": False},
    )

    learned = _weights_to_dict(result.x)
    learned_brier = result.fun

    print(f"\n  默认权重:  Elo={DEFAULT_WEIGHTS['elo']:.2f} Poisson={DEFAULT_WEIGHTS['poisson']:.2f} "
          f"Players={DEFAULT_WEIGHTS['players']:.2f} Market={DEFAULT_WEIGHTS['market']:.2f}")
    print(f"  默认 Brier: {default_brier:.4f}")
    print(f"\n  学习权重:  Elo={learned['elo']:.2f} Poisson={learned['poisson']:.2f} "
          f"Players={learned['players']:.2f} Market={learned['market']:.2f}")
    print(f"  学习 Brier: {learned_brier:.4f}")
    print(f"  改善: {(default_brier - learned_brier) / default_brier * 100:.2f}%")

    # Step 4: 按联赛分别优化
    by_league = defaultdict(list)
    for entry in precomputed:
        by_league[entry["competition"]].append(entry)

    print(f"\n  各联赛权重:")
    print(f"  {'联赛':<12} {'Elo':<6} {'Poisson':<8} {'Players':<8} {'Market':<8} {'Brier':<8}")
    print(f"  {'-'*56}")

    league_weights = {}
    for comp in sorted(by_league.keys()):
        data = by_league[comp]
        if len(data) < 50:
            print(f"  {comp:<12} 样本不足 ({len(data)} 场)")
            continue
        res = minimize(
            fun=_fast_fuse_and_brier,
            x0=x0,
            args=(data,),
            method="L-BFGS-B",
            bounds=bounds,
            options={"maxiter": 300, "disp": False},
        )
        lw = _weights_to_dict(res.x)
        lb = res.fun
        league_weights[comp] = lw
        print(f"  {comp:<12} {lw['elo']:<6.2f} {lw['poisson']:<8.2f} {lw['players']:<8.2f} "
              f"{lw['market']:<8.2f} {lb:<8.4f}")

    # Step 5: 有/无赔率子集对比
    with_odds = [e for e in precomputed if e["has_real_odds"]]
    without_odds = [e for e in precomputed if not e["has_real_odds"]]

    if with_odds and without_odds:
        brier_with = _fast_fuse_and_brier(with_odds, result.x)
        brier_without = _fast_fuse_and_brier(without_odds, result.x)
        print(f"\n  有赔率子集: {len(with_odds)} 场, Brier={brier_with:.4f}")
        print(f"  无赔率子集: {len(without_odds)} 场, Brier={brier_without:.4f}")

    return {
        "global": learned,
        "global_brier": learned_brier,
        "default_brier": default_brier,
        "by_league": league_weights,
        "sample_size": len(precomputed),
    }


# ────────────────────────────
# 保存结果到数据库
# ────────────────────────────

def save_backtest_snapshot(db, results: Dict, weights: Dict, label: str):
    """保存回测快照到 accuracy_snapshots 表"""
    from database.models import AccuracySnapshot
    snapshot = AccuracySnapshot(
        snapshot_type="backtest",
        metric="combined",
        value=results.get("brier", 0),
        sample_size=results.get("n", 0),
        weights=weights,
        stage="all",
        notes=f"{label} | acc={results.get('accuracy',0)*100:.1f}% "
              f"brier={results.get('brier',0):.4f} "
              f"log_loss={results.get('log_loss',0):.4f}",
    )
    db.add(snapshot)
    db.commit()
    print(f"  保存快照: {label}")


def save_fusion_weights(db, weights: Dict, stage: str = "all", elo_range: str = "all",
                         metric_value: float = 0, sample_size: int = 0):
    """保存融合权重到 fusion_weights 表"""
    from database.models import FusionWeight
    # 标记旧权重为 inactive
    db.query(FusionWeight).filter(
        FusionWeight.stage == stage,
        FusionWeight.elo_diff_range == elo_range,
    ).update({"is_active": False}, synchronize_session=False)

    fw = FusionWeight(
        stage=stage,
        elo_diff_range=elo_range,
        weights=weights,
        metric="brier",
        metric_value=metric_value,
        sample_size=sample_size,
        is_active=True,
    )
    db.add(fw)
    db.commit()
    print(f"  保存权重: {stage}/{elo_range}")


# ────────────────────────────
# 主入口
# ────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser(description="数据库联赛 walk-forward 回测")
    parser.add_argument("--calibrate-dc", action="store_true",
                        help="校准 Dixon-Coles rho + DRAW_INFLATION")
    parser.add_argument("--learn-weights", action="store_true",
                        help="学习最优融合权重")
    parser.add_argument("--calibrate-ht", action="store_true",
                        help="校准半场比分 (HALF)")
    parser.add_argument("--save", action="store_true",
                        help="保存结果到数据库")
    parser.add_argument("--full", action="store_true",
                        help="完整流程: 回测 + DC校准 + 权重学习 + 半场校准")
    args = parser.parse_args()

    from database.models import SessionLocal
    db = SessionLocal()

    try:
        matches = fetch_finished_league_matches(db)
        print(f"数据库联赛比赛: {len(matches)} 场")

        if not matches:
            print("⚠️ 无已结束的联赛比赛，请先导入数据")
            sys.exit(1)

        # 1. 默认权重回测
        print(f"\n{'='*60}")
        print("  默认权重回测")
        print(f"{'='*60}")
        results = run_backtest(matches, DEFAULT_WEIGHTS.copy(), "默认权重")

        if args.save and results:
            save_backtest_snapshot(db, results, DEFAULT_WEIGHTS, "db_league_default")

        # 2. Dixon-Coles 校准
        if args.calibrate_dc or args.full:
            opt_rho, opt_di = calibrate_dixon_coles(matches)
            if args.save:
                print(f"\n  建议更新 prediction_engine.py:")
                print(f"    DIXON_COLES_RHO = {opt_rho:.4f}")
                print(f"    DRAW_INFLATION_FACTOR = {opt_di:.4f}")

        # 3. 权重学习
        if args.learn_weights or args.full:
            weight_results = learn_weights_with_wf_data(db, matches)
            if args.save and weight_results:
                global_w = weight_results["global"]
                save_fusion_weights(
                    db, global_w, "all", "all",
                    weight_results["global_brier"],
                    weight_results["sample_size"],
                )

        # 4. 半场校准
        if args.calibrate_ht or args.full:
            ht_results = calibrate_half_time(matches)

        # 5. 对比: 学习权重回测
        if args.learn_weights or args.full:
            if weight_results and weight_results.get("global"):
                print(f"\n{'='*60}")
                print("  学习权重回测")
                print(f"{'='*60}")
                learned_w = weight_results["global"]
                learned_results = run_backtest(matches, learned_w, "学习权重")

                if args.save and learned_results:
                    save_backtest_snapshot(db, learned_results, learned_w, "db_league_learned")

        print(f"\n{'='*60}")
        print("  完成")
        print(f"{'='*60}")

    finally:
        db.close()


if __name__ == "__main__":
    main()
