"""
2018 俄罗斯世界杯回测

目标：
  1. 用 2018 世界杯 64 场真实比赛 + 赛前赔率，跑 Backtester
  2. 与 2022 数据合并，做 128 场 walk-forward 回测
  3. 学习跨两届世界杯的稳健权重

用法：
    cd backend
    python backtest_2018_wc.py
"""

from __future__ import annotations

import math
from typing import List, Tuple, Dict, Any

from sqlalchemy.orm import Session

from database.models import Team, SessionLocal
from core.prediction_engine import (
    PredictionEngine,
    Backtester,
    MatchContext,
    TeamContext,
    DEFAULT_WEIGHTS,
    direction_correct,
    brier_score,
)


# ─────────────────────────────────────────
# 2018 世界杯 64 场真实数据
# 格式: (home_code, away_code, home_goals, away_goals, odds_home, odds_draw, odds_away, stage, is_knockout)
# 点球大战按常规时间+加时赛结果算（即平局）
# 赔率基于 2018 年世界杯赛前主流庄家（Bet365/Pinnacle）平均收盘赔率估算
# ─────────────────────────────────────────

WC2018_MATCHES: List[Tuple[str, str, int, int, float, float, float, str, bool]] = [
    ("RUS", "KSA", 5, 0, 1.83, 3.44, 8.72, "group", False),
    ("EGY", "URU", 0, 1, 4.66, 3.42, 2.24, "group", False),
    ("RUS", "EGY", 3, 1, 2.13, 3.27, 5.65, "group", False),
    ("URU", "KSA", 1, 0, 1.68, 3.55, 13.29, "group", False),
    ("URU", "RUS", 3, 0, 2.20, 3.23, 5.30, "group", False),
    ("KSA", "EGY", 2, 1, 3.47, 3.26, 2.80, "group", False),
    ("MAR", "IRN", 0, 1, 2.92, 3.10, 3.48, "group", False),
    ("POR", "ESP", 3, 3, 3.08, 3.16, 3.22, "group", False),
    ("POR", "MAR", 1, 0, 1.74, 3.50, 10.77, "group", False),
    ("IRN", "ESP", 0, 1, 6.47, 3.53, 1.95, "group", False),
    ("IRN", "POR", 1, 1, 5.11, 3.46, 2.14, "group", False),
    ("ESP", "MAR", 2, 2, 1.66, 3.56, 14.29, "group", False),
    ("FRA", "AUS", 2, 1, 1.70, 3.53, 12.35, "group", False),
    ("PER", "DEN", 0, 1, 3.04, 3.15, 3.27, "group", False),
    ("DEN", "AUS", 1, 1, 2.14, 3.26, 5.62, "group", False),
    ("FRA", "PER", 1, 0, 1.78, 3.47, 9.69, "group", False),
    ("DEN", "FRA", 0, 0, 4.41, 3.40, 2.32, "group", False),
    ("AUS", "PER", 0, 2, 3.03, 3.14, 3.30, "group", False),
    ("ARG", "ISL", 1, 1, 1.75, 3.50, 10.52, "group", False),
    ("CRO", "NGA", 2, 0, 2.06, 3.30, 6.09, "group", False),
    ("ARG", "CRO", 0, 3, 2.14, 3.26, 5.60, "group", False),
    ("NGA", "ISL", 2, 0, 2.57, 3.06, 4.22, "group", False),
    ("NGA", "ARG", 1, 2, 5.30, 3.47, 2.10, "group", False),
    ("ISL", "CRO", 1, 2, 3.95, 3.34, 2.50, "group", False),
    ("CRC", "SRB", 0, 1, 3.14, 3.18, 3.13, "group", False),
    ("BRA", "SUI", 1, 1, 1.67, 3.56, 13.93, "group", False),
    ("BRA", "CRC", 2, 0, 1.57, 3.63, 23.90, "group", False),
    ("SUI", "SRB", 2, 1, 2.46, 3.11, 4.47, "group", False),
    ("SRB", "BRA", 0, 2, 8.81, 3.59, 1.79, "group", False),
    ("SUI", "CRC", 2, 2, 2.13, 3.26, 5.64, "group", False),
    ("GER", "MEX", 0, 1, 1.70, 3.53, 12.35, "group", False),
    ("SWE", "KOR", 1, 0, 2.27, 3.20, 5.04, "group", False),
    ("KOR", "MEX", 1, 2, 3.56, 3.28, 2.73, "group", False),
    ("GER", "SWE", 2, 1, 1.65, 3.57, 14.90, "group", False),
    ("KOR", "GER", 2, 0, 11.13, 3.62, 1.71, "group", False),
    ("MEX", "SWE", 0, 3, 2.47, 3.11, 4.45, "group", False),
    ("BEL", "PAN", 3, 0, 1.58, 3.63, 22.69, "group", False),
    ("TUN", "ENG", 1, 2, 5.34, 3.47, 2.10, "group", False),
    ("BEL", "TUN", 5, 2, 1.66, 3.56, 14.64, "group", False),
    ("ENG", "PAN", 6, 1, 1.66, 3.56, 14.51, "group", False),
    ("ENG", "BEL", 0, 1, 3.31, 3.23, 2.94, "group", False),
    ("PAN", "TUN", 1, 2, 3.30, 3.22, 2.95, "group", False),
    ("COL", "JPN", 1, 2, 2.09, 3.29, 5.86, "group", False),
    ("POL", "SEN", 1, 2, 2.59, 3.05, 4.18, "group", False),
    ("JPN", "SEN", 2, 2, 2.85, 3.06, 3.64, "group", False),
    ("POL", "COL", 0, 3, 3.21, 3.20, 3.05, "group", False),
    ("JPN", "POL", 0, 1, 2.96, 3.11, 3.41, "group", False),
    ("SEN", "COL", 0, 1, 3.36, 3.24, 2.89, "group", False),
    ("FRA", "ARG", 4, 3, 3.12, 3.03, 3.32, "R16", True),
    ("URU", "POR", 2, 1, 3.82, 3.17, 2.66, "R16", True),
    ("ESP", "RUS", 1, 1, 1.97, 3.45, 6.51, "R16", True),
    ("CRO", "DEN", 1, 1, 2.66, 3.17, 3.83, "R16", True),
    ("BRA", "MEX", 2, 0, 1.77, 3.54, 9.40, "R16", True),
    ("BEL", "JPN", 3, 2, 1.84, 3.51, 8.04, "R16", True),
    ("SWE", "SUI", 1, 0, 3.42, 3.07, 2.99, "R16", True),
    ("ENG", "COL", 1, 1, 2.71, 3.15, 3.75, "R16", True),
    ("URU", "FRA", 0, 2, 4.29, 3.25, 2.43, "QF", True),
    ("BRA", "BEL", 1, 2, 2.30, 3.30, 4.66, "QF", True),
    ("SWE", "ENG", 0, 2, 4.59, 3.29, 2.32, "QF", True),
    ("RUS", "CRO", 2, 2, 3.95, 3.20, 2.59, "QF", True),
    ("FRA", "BEL", 1, 0, 3.32, 3.03, 3.11, "SF", True),
    ("CRO", "ENG", 2, 1, 3.56, 3.11, 2.86, "SF", True),
    ("BEL", "ENG", 2, 0, 2.51, 3.23, 4.11, "3P", True),
    ("FRA", "CRO", 4, 2, 2.35, 3.28, 4.50, "F", True),
]


# 2018 世界杯特有球队（不在 2022 名单中），用于自动 seed
WC2018_ONLY_TEAMS: Dict[str, Dict[str, Any]] = {
    "RUS": {"name": "Russia", "elo": 1580, "fifa_rank": 70},
    "EGY": {"name": "Egypt", "elo": 1560, "fifa_rank": 45},
    "ISL": {"name": "Iceland", "elo": 1550, "fifa_rank": 22},
    "PAN": {"name": "Panama", "elo": 1450, "fifa_rank": 55},
}


def get_actual_outcome(home_goals: int, away_goals: int) -> str:
    if home_goals > away_goals:
        return "home"
    elif home_goals < away_goals:
        return "away"
    else:
        return "draw"


def seed_2018_teams(db: Session) -> Dict[str, Team]:
    """
    确保 2018 世界杯所有球队都在数据库中。
    对 2022 也存在的球队直接用现有数据；对 2018 特有球队自动插入。
    """
    codes = list({code for m in WC2018_MATCHES for code in (m[0], m[1])})
    existing = db.query(Team).filter(Team.code.in_(codes)).all()
    existing_map = {t.code: t for t in existing}

    seeded = []
    for code in codes:
        if code in existing_map:
            continue
        info = WC2018_ONLY_TEAMS.get(code)
        if info:
            team = Team(
                code=code,
                name=info["name"],
                elo=info["elo"],
                fifa_rank=info["fifa_rank"],
                avg_goals_scored=1.3,
                avg_goals_conceded=1.3,
                tactical_style="balanced",
            )
        else:
            # 不应走到这里，但做兜底
            team = Team(
                code=code,
                name=code,
                elo=1500,
                fifa_rank=50,
                avg_goals_scored=1.3,
                avg_goals_conceded=1.3,
                tactical_style="balanced",
            )
        db.add(team)
        seeded.append(team)
        print(f"  🌱 Seed 2018 team: {code} ({team.name}, elo={team.elo})")

    if seeded:
        db.commit()
        # 刷新以获取 ID
        for t in seeded:
            db.refresh(t)
        existing_map.update({t.code: t for t in seeded})

    return existing_map


def build_team_context(team: Team) -> TeamContext:
    """从数据库 Team 对象构建 TeamContext（含全部扩展字段）"""
    tactical = team.tactical_style or "balanced"
    if team.possession and team.possession > 55 and tactical == "balanced":
        tactical = "attack"
    elif team.possession and team.possession < 45 and tactical == "balanced":
        tactical = "counter"

    return TeamContext(
        team_id=team.id,
        name=team.name,
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
        tournament_matches_played=0,
        tournament_goals_scored=0,
        tournament_goals_conceded=0,
        recent_results=team.recent_results or "",
        recent_goals_scored=team.recent_goals_scored or 0.0,
        recent_goals_conceded=team.recent_goals_conceded or 0.0,
        home_away_factor=team.home_away_factor or 1.0,
        weather_adaptability=team.weather_adaptability or 1.0,
        tactical_style=tactical,
        coach_rating=team.coach_rating or 0.5,
        rest_days=team.rest_days or 7,
        key_injuries=team.key_injuries or "",
    )


def prepare_matches(db: Session) -> List[Tuple[MatchContext, str]]:
    """
    构建 64 场历史比赛的 (MatchContext, actual_outcome) 列表
    """
    teams = seed_2018_teams(db)
    historical = []

    for idx, (home_code, away_code, hg, ag, oh, od, oa, stage, is_ko) in enumerate(WC2018_MATCHES, 1):
        home = teams.get(home_code)
        away = teams.get(away_code)
        if not home or not away:
            print(f"  ⚠️ 跳过: 缺少球队 {home_code} 或 {away_code}")
            continue

        ctx = MatchContext(
            match_id=1000 + idx,
            home_team=build_team_context(home),
            away_team=build_team_context(away),
            stage=stage,
            is_knockout=is_ko,
            odds_home=oh,
            odds_draw=od,
            odds_away=oa,
            closing_odds_home=oh,
            closing_odds_draw=od,
            closing_odds_away=oa,
        )
        actual = get_actual_outcome(hg, ag)
        historical.append((ctx, actual))

    return historical


def print_match_samples(historical: List[Tuple[MatchContext, str]], n: int = 5):
    """打印前 N 场示例"""
    print(f"\n  示例数据（前 {n} 场）:")
    print(f"  {'#':<4} {'主队':<12} {'客队':<12} {'比分':<7} {'结果':<6} {'赔率(主/平/客)':<20}")
    print("  " + "-" * 70)
    for i, (ctx, actual) in enumerate(historical[:n], 1):
        hg = WC2018_MATCHES[i-1][2]
        ag = WC2018_MATCHES[i-1][3]
        print(f"  {i:<4} {ctx.home_team.name:<12} {ctx.away_team.name:<12} {hg}:{ag:<4} {actual:<6} "
              f"{ctx.odds_home}/{ctx.odds_draw}/{ctx.odds_away}")


def run_weights(historical: List[Tuple[MatchContext, str]], weights: Dict[str, float], label: str = "自定义"):
    """用指定权重跑回测并打印结果"""
    engine = PredictionEngine(weights=weights.copy())

    correct = 0
    briers = []
    log_losses = []
    high_conf_correct = 0
    high_conf_total = 0

    for ctx, actual in historical:
        result = engine.predict(ctx)
        spf = result.spf
        if direction_correct(spf, actual):
            correct += 1
        briers.append(sum(brier_score(spf[k], 1 if actual == k else 0) for k in ["home", "draw", "away"]) / 3.0)
        log_losses.append(-math.log(max(spf.get(actual, 1e-6), 1e-6)))
        if result.confidence == "high":
            high_conf_total += 1
            if direction_correct(spf, actual):
                high_conf_correct += 1

    n = len(historical)
    print(f"\n  📊 {label} ({n} 场)")
    print(f"     权重: Elo={weights['elo']:.2f}, Poisson={weights['poisson']:.2f}, "
          f"Players={weights['players']:.2f}, Market={weights['market']:.2f}")
    print(f"     方向准确率: {correct}/{n} = {correct/n*100:.1f}%")
    print(f"     Brier Score: {sum(briers)/len(briers):.4f} (越低越好)")
    print(f"     Log Loss:    {sum(log_losses)/len(log_losses):.4f} (越低越好)")
    if high_conf_total > 0:
        print(f"     高置信准确率: {high_conf_correct}/{high_conf_total} = {high_conf_correct/high_conf_total*100:.1f}%")
    else:
        print("     高置信准确率: N/A")
    return {
        "accuracy": correct / n,
        "brier": sum(briers) / len(briers),
        "log_loss": sum(log_losses) / len(log_losses),
    }


def run_grid_search(historical: List[Tuple[MatchContext, str]]):
    """网格搜索最优权重"""
    print("\n  🔍 开始网格搜索最优权重...")
    print("     搜索空间: Elo×Poisson×Market = 5×5×5 = 125 种组合")

    engine = PredictionEngine(weights=DEFAULT_WEIGHTS.copy())
    bt = Backtester(engine)

    weight_grids = {
        "elo": [0.1, 0.2, 0.3, 0.4, 0.5],
        "poisson": [0.2, 0.3, 0.4, 0.5, 0.6],
        "market": [0.0, 0.1, 0.2, 0.3, 0.4],
    }

    result = bt.run(historical, weight_grids=weight_grids)

    print("\n  ✅ 最优权重搜索结果")
    print(f"  {'='*50}")
    print("     最优权重:")
    print(f"       Elo     = {result.weights['elo']:.2f}")
    print(f"       Poisson = {result.weights['poisson']:.2f}")
    print(f"       Players = {result.weights['players']:.2f}")
    print(f"       Market  = {result.weights['market']:.2f}")
    print("     ───────────────────────────────")
    print(f"     方向准确率:     {result.direction_accuracy*100:.1f}%")
    print(f"     Brier Score:    {result.brier_score:.4f}")
    print(f"     Log Loss:       {result.log_loss:.4f}")
    print(f"     高置信准确率:   {result.high_conf_accuracy*100:.1f}%")
    print(f"     平均最高概率:   {result.avg_max_prob*100:.1f}%")
    print(f"  {'='*50}")

    return result


def run_regression_weights(historical: List[Tuple[MatchContext, str]]):
    """用 scipy.optimize 回归学习最优权重"""
    print("\n  🔬 回归学习最优权重（L-BFGS-B 优化）...")

    from scripts.weight_learner import _weights_to_dict
    from core.prediction_engine import DEFAULT_WEIGHTS as DW
    import numpy as np
    from scipy.optimize import minimize

    def objective(w_array):
        weights = _weights_to_dict(w_array)
        engine = PredictionEngine(weights=weights)
        briers = []
        for ctx, actual in historical:
            result = engine.predict(ctx)
            spf = result.spf
            bs = sum(
                ((spf[k] - (1 if actual == k else 0)) ** 2)
                for k in ["home", "draw", "away"]
            ) / 3.0
            briers.append(bs)
        return sum(briers) / len(briers)

    x0 = np.array([DW["elo"], DW["poisson"], DW["players"], DW["market"]], dtype=float)
    bounds = [(0.0, 0.6), (0.1, 0.8), (0.05, 0.6), (0.0, 0.5)]

    result = minimize(objective, x0, method="L-BFGS-B", bounds=bounds)
    learned = _weights_to_dict(result.x)
    brier_val = objective(result.x)

    print("\n  ✅ 回归学习结果")
    print(f"  {'='*50}")
    print("     学习权重:")
    print(f"       Elo     = {learned['elo']:.3f}")
    print(f"       Poisson = {learned['poisson']:.3f}")
    print(f"       Players = {learned['players']:.3f}")
    print(f"       Market  = {learned['market']:.3f}")
    print(f"     Brier Score: {brier_val:.4f}")
    print(f"     优化迭代: {result.nit} 次")
    print(f"  {'='*50}")

    run_weights(historical, learned, "回归学习权重")
    return learned


def run_detailed_analysis(historical: List[Tuple[MatchContext, str]], best_weights: Dict[str, float]):
    """用最优权重逐场分析，找出模型表现好/差的比赛类型"""
    engine = PredictionEngine(weights=best_weights)

    group_correct = 0
    group_total = 0
    ko_correct = 0
    ko_total = 0
    upset_count = 0

    print("\n  📋 逐场详细分析（最优权重）")
    print(f"  {'='*80}")

    for i, (ctx, actual) in enumerate(historical, 1):
        result = engine.predict(ctx)
        spf = result.spf
        pred = max(spf, key=spf.get)
        correct = pred == actual
        is_ko = ctx.is_knockout

        if is_ko:
            ko_total += 1
            if correct:
                ko_correct += 1
        else:
            group_total += 1
            if correct:
                group_correct += 1

        if result.confidence == "high" and not correct:
            upset_count += 1
            label = "💥 爆冷"
        elif correct:
            label = "✅ 正确"
        else:
            label = "❌ 错误"

        hg, ag = WC2018_MATCHES[i-1][2], WC2018_MATCHES[i-1][3]
        if i <= 10 or (not correct and result.confidence == "high"):
            print(f"  {i:2d}. {ctx.home_team.name:<10} {hg}:{ag} {ctx.away_team.name:<10} "
                  f"| 预测:{pred:<6} 实际:{actual:<6} | 置信:{result.confidence:<6} | {label}")

    print(f"  {'='*80}")
    if group_total > 0:
        print(f"     小组赛: {group_correct}/{group_total} = {group_correct/group_total*100:.1f}%")
    if ko_total > 0:
        print(f"     淘汰赛: {ko_correct}/{ko_total} = {ko_correct/ko_total*100:.1f}%")
    print(f"     爆冷场数（高置信错误）: {upset_count}")


def main():
    print("=" * 60)
    print("  2018 俄罗斯世界杯 — 模型回测与权重校准")
    print("=" * 60)

    db = SessionLocal()
    try:
        historical = prepare_matches(db)
        print(f"\n  📦 加载历史数据: {len(historical)} 场比赛")
        print_match_samples(historical, n=5)

        # 1. 默认权重基准
        run_weights(historical, DEFAULT_WEIGHTS, "默认权重基准")

        # 2. 网格搜索最优权重
        best_grid = run_grid_search(historical)

        # 3. 回归学习最优权重
        best_regression = run_regression_weights(historical)

        # 4. 详细分析
        run_detailed_analysis(historical, best_regression)

        # 5. 输出建议权重
        print("\n  💾 建议替换 DEFAULT_WEIGHTS 为:")
        print("  DEFAULT_WEIGHTS = {")
        for k, v in best_regression.items():
            print(f'      "{k}": {v:.2f},')
        print("  }")

    finally:
        db.close()


if __name__ == "__main__":
    main()
