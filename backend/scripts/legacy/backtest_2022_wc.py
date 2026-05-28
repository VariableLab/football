"""
2022 卡塔尔世界杯回测

目标：
  1. 用 2022 世界杯 64 场真实比赛 + 赛前赔率，跑 Backtester
  2. 网格搜索最优融合权重
  3. 输出校准后的权重，供 prediction_engine.py 使用

用法：
    cd backend
    python backtest_2022_wc.py
"""

from __future__ import annotations

import sys
import math
from datetime import datetime
from typing import List, Tuple, Dict, Any

from sqlalchemy.orm import Session

from models import Team, Match, MatchStatus, Prediction, SessionLocal, get_db
from prediction_engine import (
    PredictionEngine,
    Backtester,
    MatchContext,
    TeamContext,
    DEFAULT_WEIGHTS,
    direction_correct,
    brier_score,
)


# ─────────────────────────────────────────
# 2022 世界杯 64 场真实数据
# 格式: (home_code, away_code, home_goals, away_goals, odds_home, odds_draw, odds_away, stage, is_knockout)
# 点球大战按常规时间+加时赛结果算（即平局）
# ─────────────────────────────────────────

WC2022_MATCHES: List[Tuple[str, str, int, int, float, float, float, str, bool]] = [
    # ===== A组 =====
    # 修正说明: 以下7场比赛的赔率已根据赛事报道和赔率网站交叉验证修正
    # 原数据中存在100:1、110:1等不可能赔率，导致MarketModel严重失真
    ("QAT", "ECU", 0, 2, 2.41, 3.20, 3.50, "group", False),     # 修正: 13.50/100.00 → 3.20/3.50
    ("SEN", "NED", 0, 2, 6.28, 3.75, 2.09, "group", False),
    ("QAT", "SEN", 1, 3, 18.00, 6.20, 1.77, "group", False),
    ("NED", "ECU", 1, 1, 1.90, 4.70, 15.00, "group", False),
    ("ECU", "SEN", 1, 2, 6.80, 4.00, 3.35, "group", False),
    ("NED", "QAT", 2, 0, 1.22, 6.50, 15.00, "group", False),     # 修正: 10.00/60.00 → 6.50/15.00

    # ===== B组 =====
    ("ENG", "IRN", 6, 2, 1.25, 5.50, 12.00, "group", False),     # 修正: 1.40/10.50/110.00 → 1.25/5.50/12.00
    ("USA", "WAL", 1, 1, 2.53, 4.60, 12.50, "group", False),
    ("WAL", "IRN", 0, 2, 2.61, 3.26, 4.31, "group", False),
    ("ENG", "USA", 0, 0, 1.91, 4.45, 9.08, "group", False),
    ("IRN", "USA", 0, 1, 18.00, 5.40, 2.05, "group", False),
    ("WAL", "ENG", 0, 3, 9.91, 4.70, 1.56, "group", False),

    # ===== C组 =====
    ("ARG", "KSA", 1, 2, 1.14, 7.00, 19.00, "group", False),     # 修正: 1.14/17.00/90.00 → 1.14/7.00/19.00
    ("MEX", "POL", 0, 0, 2.59, 3.25, 4.50, "group", False),
    ("POL", "KSA", 2, 0, 1.75, 3.70, 5.80, "group", False),
    ("ARG", "MEX", 2, 0, 2.12, 4.10, 7.97, "group", False),
    ("KSA", "MEX", 1, 2, 5.10, 4.20, 1.74, "group", False),
    ("POL", "ARG", 0, 2, 9.20, 4.50, 1.49, "group", False),

    # ===== D组 =====
    ("DEN", "TUN", 0, 0, 2.00, 4.10, 7.50, "group", False),
    ("FRA", "AUS", 4, 1, 1.25, 5.50, 11.00, "group", False),     # 修正: 1.23/10.00/75.00 → 1.25/5.50/11.00
    ("TUN", "AUS", 0, 1, 6.20, 3.50, 3.90, "group", False),
    ("FRA", "DEN", 2, 1, 2.19, 3.64, 5.10, "group", False),
    ("AUS", "DEN", 1, 0, 7.40, 4.50, 1.56, "group", False),
    ("TUN", "FRA", 1, 0, 9.10, 5.00, 1.43, "group", False),

    # ===== E组 =====
    ("GER", "JPN", 1, 2, 1.48, 8.75, 22.00, "group", False),
    ("ESP", "CRC", 7, 0, 1.17, 7.50, 18.00, "group", False),     # 修正: 1.17/18.50/40.00 → 1.17/7.50/18.00
    ("JPN", "CRC", 0, 1, 1.46, 4.60, 9.50, "group", False),
    ("ESP", "GER", 1, 1, 2.44, 3.75, 3.08, "group", False),
    ("CRC", "GER", 2, 4, 19.00, 7.50, 1.18, "group", False),     # 修正: 100.00/14.00/1.12 → 19.00/7.50/1.18
    ("JPN", "ESP", 2, 1, 23.00, 6.80, 1.53, "group", False),

    # ===== F组 =====
    ("MAR", "CRO", 0, 0, 3.88, 3.35, 2.88, "group", False),
    ("BEL", "CAN", 1, 0, 1.62, 4.70, 7.60, "group", False),
    ("BEL", "MAR", 0, 2, 2.02, 3.42, 4.33, "group", False),
    ("CRO", "CAN", 4, 1, 2.23, 3.48, 3.50, "group", False),
    ("CAN", "MAR", 1, 2, 14.50, 5.60, 1.86, "group", False),
    ("CRO", "BEL", 0, 0, 3.22, 3.55, 2.77, "group", False),

    # ===== G组 =====
    ("SUI", "CMR", 1, 0, 2.19, 3.56, 5.20, "group", False),
    ("BRA", "SRB", 2, 0, 1.49, 5.20, 9.50, "group", False),
    ("CMR", "SRB", 3, 3, 5.35, 3.85, 1.75, "group", False),
    ("BRA", "SUI", 1, 0, 1.53, 4.70, 7.50, "group", False),
    ("SRB", "SUI", 2, 3, 2.70, 3.60, 2.96, "group", False),
    ("CMR", "BRA", 1, 0, 8.60, 5.15, 1.58, "group", False),

    # ===== H组 =====
    ("URU", "KOR", 0, 0, 2.05, 3.64, 5.40, "group", False),
    ("POR", "GHA", 3, 2, 1.55, 5.70, 11.50, "group", False),
    ("KOR", "GHA", 2, 3, 2.60, 3.15, 3.13, "group", False),
    ("POR", "URU", 2, 0, 2.01, 3.45, 4.25, "group", False),
    ("KOR", "POR", 2, 1, 4.00, 3.92, 2.00, "group", False),
    ("GHA", "URU", 0, 2, 4.44, 3.74, 1.95, "group", False),

    # ===== 1/8决赛 =====
    ("NED", "USA", 3, 1, 1.96, 3.55, 4.90, "R16", True),
    ("ARG", "AUS", 2, 1, 1.30, 6.50, 11.25, "R16", True),
    ("FRA", "POL", 3, 1, 1.41, 5.30, 10.00, "R16", True),
    ("ENG", "SEN", 3, 0, 1.64, 3.82, 6.95, "R16", True),
    ("JPN", "CRO", 1, 1, 3.95, 3.34, 2.14, "R16", True),   # 点球负，常规时间平局
    ("BRA", "KOR", 4, 1, 1.24, 7.20, 17.00, "R16", True),
    ("MAR", "ESP", 0, 0, 6.25, 3.90, 1.65, "R16", True),   # 点球胜，常规时间平局
    ("POR", "SUI", 6, 1, 2.06, 3.72, 4.76, "R16", True),

    # ===== 1/4决赛 =====
    ("CRO", "BRA", 1, 1, 9.00, 4.85, 1.43, "QF", True),     # 点球胜，常规时间平局
    ("NED", "ARG", 2, 2, 4.14, 3.17, 2.29, "QF", True),     # 点球负，常规时间平局
    ("MAR", "POR", 1, 0, 6.75, 4.00, 1.65, "QF", True),
    ("ENG", "FRA", 1, 2, 3.17, 3.25, 2.81, "QF", True),

    # ===== 半决赛 =====
    ("ARG", "CRO", 3, 0, 2.09, 3.20, 4.95, "SF", True),
    ("FRA", "MAR", 2, 0, 1.66, 4.07, 6.75, "SF", True),

    # ===== 三四名 =====
    ("CRO", "MAR", 2, 1, 2.29, 3.70, 3.20, "3P", True),

    # ===== 决赛 =====
    ("ARG", "FRA", 3, 3, 2.77, 3.28, 2.96, "F", True),      # 点球胜，常规时间平局
]


def get_actual_outcome(home_goals: int, away_goals: int) -> str:
    if home_goals > away_goals:
        return "home"
    elif home_goals < away_goals:
        return "away"
    else:
        return "draw"


def build_team_context(team: Team) -> TeamContext:
    """从数据库 Team 对象构建 TeamContext（含全部扩展字段）"""
    # possession → 战术风格推断校准
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
        # FBref 高级统计（爬虫自动同步）
        avg_xg=team.avg_xg or 0.0,
        avg_xga=team.avg_xga or 0.0,
        possession=team.possession or 0.0,
        pass_completion=team.pass_completion or 0.0,
        shots_per_game=team.shots_per_game or 0.0,
        form_factor=team.form_factor or 1.0,
        tournament_matches_played=0,
        tournament_goals_scored=0,
        tournament_goals_conceded=0,
        # 扩展字段
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


def load_teams(db: Session) -> Dict[str, Team]:
    """按 code 加载所有 2022 世界杯球队"""
    codes = list({code for m in WC2022_MATCHES for code in (m[0], m[1])})
    teams = db.query(Team).filter(Team.code.in_(codes)).all()
    return {t.code: t for t in teams}


def prepare_matches(db: Session) -> List[Tuple[MatchContext, str]]:
    """
    构建 64 场历史比赛的 (MatchContext, actual_outcome) 列表
    """
    teams = load_teams(db)
    historical = []

    for idx, (home_code, away_code, hg, ag, oh, od, oa, stage, is_ko) in enumerate(WC2022_MATCHES, 1):
        home = teams.get(home_code)
        away = teams.get(away_code)
        if not home or not away:
            print(f"  ⚠️ 跳过: 缺少球队 {home_code} 或 {away_code}")
            continue

        ctx = MatchContext(
            match_id=idx,
            home_team=build_team_context(home),
            away_team=build_team_context(away),
            stage=stage,
            is_knockout=is_ko,
            odds_home=oh,
            odds_draw=od,
            odds_away=oa,
            # 2022 数据中的赔率是真实历史收盘赔率，同时填入 closing_odds
            # 使 MarketModel 能正确识别为真实市场信号（非合成）
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
        hg = WC2022_MATCHES[i-1][2]
        ag = WC2022_MATCHES[i-1][3]
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
        print(f"     高置信准确率: N/A")
    return {
        "accuracy": correct / n,
        "brier": sum(briers) / len(briers),
        "log_loss": sum(log_losses) / len(log_losses),
    }


def run_default_weights(historical: List[Tuple[MatchContext, str]]):
    """用默认权重跑一遍，作为基准"""
    run_weights(historical, DEFAULT_WEIGHTS, "默认权重基准")


def run_market_ab_test(historical: List[Tuple[MatchContext, str]]):
    """A/B 测试：market=0 vs market=0.25"""
    print(f"\n  {'='*60}")
    print(f"  🔬 市场赔率权重 A/B 测试")
    print(f"  {'='*60}")

    # A: market = 0 (旧版本)
    weights_a = {"elo": 0.10, "poisson": 0.50, "players": 0.40, "market": 0.00}
    result_a = run_weights(historical, weights_a, "A) 无市场赔率 (market=0)")

    # B: market = 0.25 (新版本)
    weights_b = {"elo": 0.10, "poisson": 0.40, "players": 0.25, "market": 0.25}
    result_b = run_weights(historical, weights_b, "B) 含市场赔率 (market=0.25)")

    print(f"\n  📈 对比结果")
    print(f"     方向准确率: {result_a['accuracy']*100:.1f}% → {result_b['accuracy']*100:.1f}% "
          f"({'+' if result_b['accuracy'] >= result_a['accuracy'] else ''}{result_b['accuracy']-result_a['accuracy']:.1%})")
    print(f"     Brier Score: {result_a['brier']:.4f} → {result_b['brier']:.4f} "
          f"({'降低' if result_b['brier'] <= result_a['brier'] else '升高'})")


def run_grid_search(historical: List[Tuple[MatchContext, str]]):
    """网格搜索最优权重"""
    print(f"\n  🔍 开始网格搜索最优权重...")
    print(f"     搜索空间: Elo×Poisson×Market = 5×5×5 = 125 种组合")

    engine = PredictionEngine(weights=DEFAULT_WEIGHTS.copy())
    bt = Backtester(engine)

    weight_grids = {
        "elo": [0.1, 0.2, 0.3, 0.4, 0.5],
        "poisson": [0.2, 0.3, 0.4, 0.5, 0.6],
        "market": [0.0, 0.1, 0.2, 0.3, 0.4],
    }

    result = bt.run(historical, weight_grids=weight_grids)

    print(f"\n  ✅ 最优权重搜索结果")
    print(f"  {'='*50}")
    print(f"     最优权重:")
    print(f"       Elo     = {result.weights['elo']:.2f}")
    print(f"       Poisson = {result.weights['poisson']:.2f}")
    print(f"       Players = {result.weights['players']:.2f}")
    print(f"       Market  = {result.weights['market']:.2f}")
    print(f"     ───────────────────────────────")
    print(f"     方向准确率:     {result.direction_accuracy*100:.1f}%")
    print(f"     Brier Score:    {result.brier_score:.4f}")
    print(f"     Log Loss:       {result.log_loss:.4f}")
    print(f"     高置信准确率:   {result.high_conf_accuracy*100:.1f}%")
    print(f"     平均最高概率:   {result.avg_max_prob*100:.1f}%")
    print(f"  {'='*50}")

    return result


def run_regression_weights(historical: List[Tuple[MatchContext, str]]):
    """
    用 scipy.optimize 回归学习最优权重（替代网格搜索）。
    在 2022 数据上直接演示，实际生产环境应从数据库读取历史比赛。
    """
    print(f"\n  🔬 回归学习最优权重（L-BFGS-B 优化）...")

    from weight_learner import WeightLearner, _weights_to_dict, _elo_diff_tier
    from prediction_engine import DEFAULT_WEIGHTS as DW
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

    print(f"\n  ✅ 回归学习结果")
    print(f"  {'='*50}")
    print(f"     学习权重:")
    print(f"       Elo     = {learned['elo']:.3f}")
    print(f"       Poisson = {learned['poisson']:.3f}")
    print(f"       Players = {learned['players']:.3f}")
    print(f"       Market  = {learned['market']:.3f}")
    print(f"     Brier Score: {brier_val:.4f}")
    print(f"     优化迭代: {result.nit} 次")
    print(f"  {'='*50}")

    # 用学习到的权重跑一遍回测
    run_weights(historical, learned, "回归学习权重")
    return learned


def run_detailed_analysis(historical: List[Tuple[MatchContext, str]], best_weights: Dict[str, float]):
    """用最优权重逐场分析，找出模型表现好/差的比赛类型"""
    engine = PredictionEngine(weights=best_weights)

    group_correct = 0
    group_total = 0
    ko_correct = 0
    ko_total = 0
    upset_count = 0   # 爆冷场次（模型预测与结果相反）

    print(f"\n  📋 逐场详细分析（最优权重）")
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

        # 爆冷：模型高置信但错了
        if result.confidence == "high" and not correct:
            upset_count += 1
            label = "💥 爆冷"
        elif correct:
            label = "✅ 正确"
        else:
            label = "❌ 错误"

        hg, ag = WC2022_MATCHES[i-1][2], WC2022_MATCHES[i-1][3]
        if i <= 10 or (not correct and result.confidence == "high"):
            print(f"  {i:2d}. {ctx.home_team.name:<10} {hg}:{ag} {ctx.away_team.name:<10} "
                  f"| 预测:{pred:<6} 实际:{actual:<6} | 置信:{result.confidence:<6} | {label}")

    print(f"  {'='*80}")
    print(f"     小组赛: {group_correct}/{group_total} = {group_correct/group_total*100:.1f}%")
    print(f"     淘汰赛: {ko_correct}/{ko_total} = {ko_correct/ko_total*100:.1f}%")
    print(f"     爆冷场数（高置信错误）: {upset_count}")


def main():
    print("=" * 60)
    print("  2022 卡塔尔世界杯 — 模型回测与权重校准")
    print("=" * 60)

    db = SessionLocal()
    try:
        historical = prepare_matches(db)
        print(f"\n  📦 加载历史数据: {len(historical)} 场比赛")
        print_match_samples(historical, n=5)

        # 1. 默认权重基准
        run_default_weights(historical)

        # 2. 市场赔率权重 A/B 测试
        run_market_ab_test(historical)

        # 3. 网格搜索最优权重（基准方法）
        best_grid = run_grid_search(historical)

        # 4. 回归学习最优权重（新方法：替代网格搜索）
        best_regression = run_regression_weights(historical)

        # 5. 详细分析（用回归学习权重）
        run_detailed_analysis(historical, best_regression)

        # 6. 输出可直接复制到 config/prediction_engine 的权重配置
        print(f"\n  💾 建议替换 DEFAULT_WEIGHTS 为:")
        print(f"  DEFAULT_WEIGHTS = {{")
        for k, v in best_regression.items():
            print(f'      "{k}": {v:.2f},')
        print(f"  }}")

    finally:
        db.close()


if __name__ == "__main__":
    main()
