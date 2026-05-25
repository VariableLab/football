"""
遍历所有比赛，找出 market 信号最强的场次（market 概率与 poisson 概率差异最大）。
验证 MarketModel 是否真正生效。
"""

from core.prediction_engine import (
    PredictionEngine, EloModel, PoissonModel, MarketModel, PlayerAdjustmentModel,
    build_context_from_match, EnsembleFusion, DEFAULT_WEIGHTS,
)
from database.models import SessionLocal, Match


def main():
    db = SessionLocal()
    try:
        matches = db.query(Match).filter(Match.closing_odds_home.isnot(None)).all()

        results = []
        for m in matches:
            ctx = build_context_from_match(m)

            elo = EloModel.predict(ctx)
            poisson = PoissonModel.predict(ctx)["spf"]
            market = MarketModel.predict(ctx)
            players = PlayerAdjustmentModel.predict(ctx)

            if not market:
                continue

            # 计算 market 与 poisson 的分歧
            disagreement = sum(abs(poisson[k] - market[k]) for k in poisson) / 2

            # 模拟融合（带 market 权重）
            fusion_with = EnsembleFusion(DEFAULT_WEIGHTS.copy())
            fused_with = fusion_with.fuse_spf(elo, poisson, players, market, ctx)

            # 模拟融合（不带 market）
            fusion_without = EnsembleFusion({"elo": 0.10, "poisson": 0.60, "players": 0.30, "market": 0.00})
            fused_without = fusion_without.fuse_spf(elo, poisson, players, None, ctx)

            diff = sum(abs(fused_with[k] - fused_without[k]) for k in fused_with) / 2

            results.append({
                "match_code": m.match_code,
                "home": m.home_team.name,
                "away": m.away_team.name,
                "disagreement": disagreement,
                "diff": diff,
                "market_home": market["home"],
                "poisson_home": poisson["home"],
                "fused_with_home": fused_with["home"],
                "fused_without_home": fused_without["home"],
            })

        # 按分歧度排序
        results.sort(key=lambda x: x["disagreement"], reverse=True)

        print("=== Market 信号最强的 10 场比赛 ===")
        for r in results[:10]:
            print(
                f"{r['home']} vs {r['away']}: "
                f"market={r['market_home']:.1%} poisson={r['poisson_home']:.1%} "
                f"分歧={r['disagreement']:.1%} 融合差异={r['diff']:.2%}"
            )

        print(f"\n=== 统计 ===")
        print(f"总比赛数: {len(results)}")
        print(f"平均分歧: {sum(r['disagreement'] for r in results)/len(results):.1%}")
        print(f"最大分歧: {results[0]['disagreement']:.1%}")
        print(f"平均融合差异: {sum(r['diff'] for r in results)/len(results):.2%}")

        if results[0]["diff"] > 0.001:
            print("结论: MarketModel 已生效，对融合结果有实际影响。")
        else:
            print("结论: MarketModel 影响微弱，可能需要调高权重或找差异更大的赔率。")

    finally:
        db.close()


if __name__ == "__main__":
    main()
