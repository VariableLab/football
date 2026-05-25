"""
最终验证：对比"升级前"和"升级后"的预测差异。
升级前：market=0, tactical_style=balanced, xG=0（回退到 avg_goals）
升级后：market=0.15, tactical_style 从 possession 推断, xG 优先使用
"""

from core.prediction_engine import (
    PredictionEngine, EloModel, PoissonModel, MarketModel,
    PlayerAdjustmentModel, TacticalModel, EnsembleFusion,
    build_context_from_match, DEFAULT_WEIGHTS, MatchContext, TeamContext,
)
from database.models import SessionLocal, Match


def make_ctx_old_logic(match):
    """模拟升级前的逻辑"""
    ctx = build_context_from_match(match)
    # 清空 xG
    ctx.home_team.avg_xg = 0.0
    ctx.home_team.avg_xga = 0.0
    ctx.away_team.avg_xg = 0.0
    ctx.away_team.avg_xga = 0.0
    # 重置战术风格
    ctx.home_team.tactical_style = "balanced"
    ctx.away_team.tactical_style = "balanced"
    # 清空 possession
    ctx.home_team.possession = 50.0
    ctx.away_team.possession = 50.0
    return ctx


def main():
    db = SessionLocal()
    try:
        matches = db.query(Match).filter(Match.closing_odds_home.isnot(None)).all()

        total_diff = 0
        max_diff = 0
        max_match = None
        count_significant = 0

        for m in matches:
            ctx_new = build_context_from_match(m)
            ctx_old = make_ctx_old_logic(m)

            # 升级后（新权重 + 新数据）
            fusion_new = EnsembleFusion(DEFAULT_WEIGHTS.copy())
            elo_new = EloModel.predict(ctx_new)
            poisson_new = PoissonModel.predict(ctx_new)["spf"]
            market_new = MarketModel.predict(ctx_new)
            players_new = PlayerAdjustmentModel.predict(ctx_new)
            result_new = fusion_new.fuse_spf(elo_new, poisson_new, players_new, market_new, ctx_new)

            # 升级前（旧权重 + 旧数据）
            fusion_old = EnsembleFusion({"elo": 0.10, "poisson": 0.60, "players": 0.30, "market": 0.00})
            elo_old = EloModel.predict(ctx_old)
            poisson_old = PoissonModel.predict(ctx_old)["spf"]
            market_old = MarketModel.predict(ctx_old)
            players_old = PlayerAdjustmentModel.predict(ctx_old)
            result_old = fusion_old.fuse_spf(elo_old, poisson_old, players_old, market_old, ctx_old)

            diff = sum(abs(result_new[k] - result_old[k]) for k in result_new) / 2
            total_diff += diff

            if diff > max_diff:
                max_diff = diff
                max_match = m

            if diff > 0.01:
                count_significant += 1

        avg_diff = total_diff / len(matches) if matches else 0

        print("=== 系统升级效果验证 ===")
        print(f"验证比赛数: {len(matches)}")
        print(f"平均概率差异: {avg_diff:.2%}")
        print(f"最大概率差异: {max_diff:.2%}")
        print(f"显著变化场次(>1%): {count_significant}/{len(matches)} ({count_significant/len(matches):.1%})")

        if max_match:
            print(f"\n变化最大的比赛: {max_match.home_team.name} vs {max_match.away_team.name}")

        if avg_diff > 0.005:
            print("\n结论: 升级有效，预测结果产生了系统性变化。")
        else:
            print("\n结论: 升级影响微弱，数据或权重需要进一步调整。")

    finally:
        db.close()


if __name__ == "__main__":
    main()
