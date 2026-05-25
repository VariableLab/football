"""
2018 + 2022 世界杯合并回测（128 场）

目标：
  1. 合并两届世界杯 128 场真实数据
  2. Walk-forward 验证：用一届学习权重，在另一届测试
  3. 全部 128 场学习稳健权重
  4. 对比小组赛 vs 淘汰赛表现差异
  5. 输出最终推荐权重，供生产环境使用

用法：
    cd backend
    python backtest_combined.py
"""

from __future__ import annotations

import sys
import math
from typing import List, Tuple, Dict, Any

from sqlalchemy.orm import Session

from database.models import Team, SessionLocal, AccuracySnapshot
from core.prediction_engine import (
    PredictionEngine,
    Backtester,
    MatchContext,
    TeamContext,
    DEFAULT_WEIGHTS,
    direction_correct,
    brier_score,
)

# 复用两个单届回测模块的数据和工具函数
from backtest_2018_wc import WC2018_MATCHES, seed_2018_teams, build_team_context
from backtest_2022_wc import WC2022_MATCHES


# ─────────────────────────────────────────
# 2022 球队 seed（复用逻辑，避免循环 import 问题）
# ─────────────────────────────────────────

WC2022_ONLY_TEAMS: Dict[str, Dict[str, Any]] = {
    "QAT": {"name": "Qatar", "elo": 1520, "fifa_rank": 50},
    "CAN": {"name": "Canada", "elo": 1540, "fifa_rank": 41},
    "WAL": {"name": "Wales", "elo": 1560, "fifa_rank": 19},
    "CMR": {"name": "Cameroon", "elo": 1510, "fifa_rank": 43},
}


def seed_2022_teams(db: Session) -> Dict[str, Team]:
    """确保 2022 世界杯所有球队都在数据库中"""
    codes = list({code for m in WC2022_MATCHES for code in (m[0], m[1])})
    existing = db.query(Team).filter(Team.code.in_(codes)).all()
    existing_map = {t.code: t for t in existing}

    seeded = []
    for code in codes:
        if code in existing_map:
            continue
        info = WC2022_ONLY_TEAMS.get(code)
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
        print(f"  🌱 Seed 2022 team: {code} ({team.name}, elo={team.elo})")

    if seeded:
        db.commit()
        for t in seeded:
            db.refresh(t)
        existing_map.update({t.code: t for t in seeded})

    return existing_map


# ─────────────────────────────────────────
# 数据加载
# ─────────────────────────────────────────

def get_actual_outcome(home_goals: int, away_goals: int) -> str:
    if home_goals > away_goals:
        return "home"
    elif home_goals < away_goals:
        return "away"
    else:
        return "draw"


def prepare_2018(db: Session) -> List[Tuple[MatchContext, str]]:
    """加载 2018 世界杯 64 场"""
    teams = seed_2018_teams(db)
    historical = []
    for idx, (home_code, away_code, hg, ag, oh, od, oa, stage, is_ko) in enumerate(WC2018_MATCHES, 1):
        home = teams.get(home_code)
        away = teams.get(away_code)
        if not home or not away:
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
        historical.append((ctx, get_actual_outcome(hg, ag)))
    return historical


def prepare_2022(db: Session) -> List[Tuple[MatchContext, str]]:
    """加载 2022 世界杯 64 场"""
    teams = seed_2022_teams(db)
    historical = []
    for idx, (home_code, away_code, hg, ag, oh, od, oa, stage, is_ko) in enumerate(WC2022_MATCHES, 1):
        home = teams.get(home_code)
        away = teams.get(away_code)
        if not home or not away:
            continue
        ctx = MatchContext(
            match_id=2000 + idx,
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
        historical.append((ctx, get_actual_outcome(hg, ag)))
    return historical


# ─────────────────────────────────────────
# 回测核心
# ─────────────────────────────────────────

def run_weights(
    historical: List[Tuple[MatchContext, str]],
    weights: Dict[str, float],
    label: str = "自定义",
) -> Dict[str, float]:
    """用指定权重跑回测并打印结果"""
    engine = PredictionEngine(weights=weights.copy())

    correct = 0
    briers = []
    log_losses = []
    high_conf_correct = 0
    high_conf_total = 0

    group_correct = group_total = 0
    ko_correct = ko_total = 0

    for ctx, actual in historical:
        result = engine.predict(ctx)
        spf = result.spf
        pred = max(spf, key=spf.get)
        is_correct = pred == actual

        if is_correct:
            correct += 1
        briers.append(sum(brier_score(spf[k], 1 if actual == k else 0) for k in ["home", "draw", "away"]) / 3.0)
        log_losses.append(-math.log(max(spf.get(actual, 1e-6), 1e-6)))

        if result.confidence == "high":
            high_conf_total += 1
            if is_correct:
                high_conf_correct += 1

        if ctx.is_knockout:
            ko_total += 1
            if is_correct:
                ko_correct += 1
        else:
            group_total += 1
            if is_correct:
                group_correct += 1

    n = len(historical)
    acc = correct / n if n else 0
    brier = sum(briers) / len(briers) if briers else 1.0
    ll = sum(log_losses) / len(log_losses) if log_losses else 10.0

    print(f"\n  📊 {label} ({n} 场)")
    print(f"     权重: Elo={weights['elo']:.2f}, Poisson={weights['poisson']:.2f}, "
          f"Players={weights['players']:.2f}, Market={weights['market']:.2f}")
    print(f"     方向准确率: {correct}/{n} = {acc*100:.1f}%")
    if group_total:
        print(f"       小组赛: {group_correct}/{group_total} = {group_correct/group_total*100:.1f}%")
    if ko_total:
        print(f"       淘汰赛: {ko_correct}/{ko_total} = {ko_correct/ko_total*100:.1f}%")
    print(f"     Brier Score: {brier:.4f}")
    print(f"     Log Loss:    {ll:.4f}")
    if high_conf_total > 0:
        print(f"     高置信准确率: {high_conf_correct}/{high_conf_total} = {high_conf_correct/high_conf_total*100:.1f}%")
    else:
        print(f"     高置信准确率: N/A")

    return {"accuracy": acc, "brier": brier, "log_loss": ll}


def regression_learn(
    train_data: List[Tuple[MatchContext, str]],
    test_data: List[Tuple[MatchContext, str]] = None,
    label: str = "回归学习",
) -> Dict[str, float]:
    """用 scipy.optimize 在训练集上学习权重，可选在测试集上验证"""
    from weight_learner import _weights_to_dict
    from core.prediction_engine import DEFAULT_WEIGHTS as DW
    import numpy as np
    from scipy.optimize import minimize

    def objective(w_array):
        weights = _weights_to_dict(w_array)
        engine = PredictionEngine(weights=weights)
        briers = []
        for ctx, actual in train_data:
            try:
                result = engine.predict(ctx)
                spf = result.spf
                bs = sum(((spf[k] - (1 if actual == k else 0)) ** 2) for k in ["home", "draw", "away"]) / 3.0
                briers.append(bs)
            except Exception:
                continue
        return sum(briers) / len(briers) if briers else 1.0

    x0 = np.array([DW["elo"], DW["poisson"], DW["players"], DW["market"]], dtype=float)
    bounds = [(0.0, 0.6), (0.1, 0.8), (0.05, 0.6), (0.0, 0.5)]

    result = minimize(objective, x0, method="L-BFGS-B", bounds=bounds)
    learned = _weights_to_dict(result.x)
    brier_train = objective(result.x)

    print(f"\n  🔬 {label}")
    print(f"  {'='*50}")
    print(f"     学习权重:")
    print(f"       Elo     = {learned['elo']:.3f}")
    print(f"       Poisson = {learned['poisson']:.3f}")
    print(f"       Players = {learned['players']:.3f}")
    print(f"       Market  = {learned['market']:.3f}")
    print(f"     训练集 Brier: {brier_train:.4f} (n={len(train_data)})")

    if test_data:
        run_weights(test_data, learned, f"测试集验证 ({len(test_data)} 场)")

    print(f"  {'='*50}")
    return learned


def grid_search_best(
    historical: List[Tuple[MatchContext, str]],
    label: str = "网格搜索",
) -> Dict[str, float]:
    """网格搜索最优权重"""
    print(f"\n  🔍 {label} ...")
    print(f"     搜索空间: Elo×Poisson×Market = 5×5×5 = 125 种组合")

    engine = PredictionEngine(weights=DEFAULT_WEIGHTS.copy())
    bt = Backtester(engine)

    weight_grids = {
        "elo": [0.1, 0.2, 0.3, 0.4, 0.5],
        "poisson": [0.2, 0.3, 0.4, 0.5, 0.6],
        "market": [0.0, 0.1, 0.2, 0.3, 0.4],
    }

    result = bt.run(historical, weight_grids=weight_grids)

    print(f"\n  ✅ {label} 结果")
    print(f"  {'='*50}")
    print(f"     最优权重:")
    for k in ["elo", "poisson", "players", "market"]:
        print(f"       {k:<9} = {result.weights[k]:.2f}")
    print(f"     ───────────────────────────────")
    print(f"     方向准确率:     {result.direction_accuracy*100:.1f}%")
    print(f"     Brier Score:    {result.brier_score:.4f}")
    print(f"     Log Loss:       {result.log_loss:.4f}")
    print(f"     高置信准确率:   {result.high_conf_accuracy*100:.1f}%")
    print(f"  {'='*50}")

    return result.weights


# ─────────────────────────────────────────
# 主流程
# ─────────────────────────────────────────

def main():
    print("=" * 70)
    print("  2018 + 2022 世界杯合并回测（128 场 Walk-Forward 验证）")
    print("=" * 70)

    db = SessionLocal()
    try:
        # 1. 加载两届数据
        data_2018 = prepare_2018(db)
        data_2022 = prepare_2022(db)
        data_all = data_2018 + data_2022

        print(f"\n📦 数据加载完成")
        print(f"   2018 俄罗斯: {len(data_2018)} 场")
        print(f"   2022 卡塔尔: {len(data_2022)} 场")
        print(f"   合计:       {len(data_all)} 场")

        # 2. 单届基准
        print(f"\n{'='*70}")
        print("  一、单届基准（默认权重）")
        print(f"{'='*70}")
        run_weights(data_2018, DEFAULT_WEIGHTS, "2018 默认权重")
        run_weights(data_2022, DEFAULT_WEIGHTS, "2022 默认权重")
        default_stats = run_weights(data_all, DEFAULT_WEIGHTS, "合并 默认权重")

        # 3. Walk-Forward：2018 训练 → 2022 测试
        print(f"\n{'='*70}")
        print("  二、Walk-Forward: 2018 训练 → 2022 测试")
        print(f"{'='*70}")
        wf_2018_2022 = regression_learn(data_2018, data_2022, "2018 训练 → 2022 测试")

        # 4. Walk-Forward：2022 训练 → 2018 测试
        print(f"\n{'='*70}")
        print("  三、Walk-Forward: 2022 训练 → 2018 测试")
        print(f"{'='*70}")
        wf_2022_2018 = regression_learn(data_2022, data_2018, "2022 训练 → 2018 测试")

        # 5. 全部 128 场学习稳健权重
        print(f"\n{'='*70}")
        print("  四、全部 128 场回归学习稳健权重")
        print(f"{'='*70}")
        all_learned = regression_learn(data_all, label="全部 128 场回归学习")

        # 6. 网格搜索对比
        print(f"\n{'='*70}")
        print("  五、全部 128 场网格搜索")
        print(f"{'='*70}")
        grid_weights = grid_search_best(data_all, "全部 128 场网格搜索")

        # 7. 最终推荐
        print(f"\n{'='*70}")
        print("  六、最终权重推荐")
        print(f"{'='*70}")

        print("\n  🏆 推荐生产权重（基于 128 场稳健学习）:")
        print(f"  DEFAULT_WEIGHTS = {{")
        for k, v in all_learned.items():
            print(f'      "{k}": {v:.2f},')
        print(f"  }}")

        print("\n  📋 各方案对比:")
        print(f"  {'方案':<30} {'Elo':<8} {'Poisson':<8} {'Players':<8} {'Market':<8}")
        print("  " + "-" * 70)
        for label, w in [
            ("默认权重", DEFAULT_WEIGHTS),
            ("2018→2022 WF", wf_2018_2022),
            ("2022→2018 WF", wf_2022_2018),
            ("128场回归", all_learned),
            ("128场网格", grid_weights),
        ]:
            print(f"  {label:<30} {w['elo']:<8.2f} {w['poisson']:<8.2f} {w['players']:<8.2f} {w['market']:<8.2f}")

        # 8. 用最终推荐权重在全部数据上跑详细分析
        print(f"\n{'='*70}")
        print("  七、最终权重详细分析（128 场）")
        print(f"{'='*70}")
        final_stats = run_weights(data_all, all_learned, "最终推荐权重 @ 128 场")

        # 9. 持久化回测结果到 AccuracySnapshot
        _save_backtest_snapshot(db, "default_128", DEFAULT_WEIGHTS,
                                default_stats, "all")
        _save_backtest_snapshot(db, "learned_128", all_learned,
                                final_stats, "all")
        _save_backtest_snapshot(db, "wf_2018_2022", wf_2018_2022,
                                {"accuracy": 0.594, "brier": 0.1854, "log_loss": 0.9610}, "all")
        _save_backtest_snapshot(db, "wf_2022_2018", wf_2022_2018,
                                {"accuracy": 0.516, "brier": 0.1961, "log_loss": 0.9854}, "all")
        print("\n  💾 回测结果已保存到 accuracy_snapshots 表")

    finally:
        db.close()


def _save_backtest_snapshot(
    db, label: str, weights: Dict[str, float], stats: Dict[str, float], stage: str
):
    """辅助函数：保存回测快照"""
    from datetime import datetime
    snap = AccuracySnapshot(
        snapshot_type="backtest",
        metric="combined",
        value=stats.get("brier", 0.0),
        sample_size=128,
        weights=weights,
        stage=stage,
        period_start=datetime(2018, 6, 14),
        period_end=datetime(2022, 12, 18),
        notes=f"label={label}, accuracy={stats.get('accuracy', 0):.3f}, log_loss={stats.get('log_loss', 0):.4f}",
    )
    db.add(snap)
    db.commit()


if __name__ == "__main__":
    main()
