"""
验证 MarketModel 是否正确消费了 closing_odds。
对比：同一比赛，使用 closing_odds vs 使用普通 odds 的预测差异。
"""

from core.prediction_engine import PredictionEngine, MatchContext, build_context_from_match
from database.models import SessionLocal, Match


def make_ctx_without_closing_odds(match):
    """构建 MatchContext，但清空 closing_odds（强制回退到普通 odds）。"""
    ctx = build_context_from_match(match)
    ctx.closing_odds_home = None
    ctx.closing_odds_draw = None
    ctx.closing_odds_away = None
    return ctx


def main():
    db = SessionLocal()
    try:
        match = db.query(Match).filter(Match.closing_odds_home.isnot(None)).first()
        if not match:
            print("No match with closing_odds found")
            return

        home = match.home_team
        away = match.away_team
        print(f"比赛: {home.name} ({home.tactical_style}) vs {away.name} ({away.tactical_style})")
        print(f"普通赔率: 主={match.odds_home} 平={match.odds_draw} 客={match.odds_away}")
        print(f"收盘赔率: 主={match.closing_odds_home} 平={match.closing_odds_draw} 客={match.closing_odds_away}")

        engine = PredictionEngine(db_session=db)

        # 使用 closing_odds
        ctx_with = build_context_from_match(match)
        result_with = engine.predict(ctx_with)

        # 不使用 closing_odds
        ctx_without = make_ctx_without_closing_odds(match)
        result_without = engine.predict(ctx_without)

        print(f"\n使用 closing_odds:")
        print(f"  SPF: 主={result_with.spf['home']:.2%} 平={result_with.spf['draw']:.2%} 客={result_with.spf['away']:.2%}")
        print(f"\n不使用 closing_odds:")
        print(f"  SPF: 主={result_without.spf['home']:.2%} 平={result_without.spf['draw']:.2%} 客={result_without.spf['away']:.2%}")

        diff = {
            k: abs(result_with.spf[k] - result_without.spf[k])
            for k in result_with.spf
        }
        print(f"\n差异: 主={diff['home']:.2%} 平={diff['draw']:.2%} 客={diff['away']:.2%}")

        if any(v > 0.001 for v in diff.values()):
            print("结论: MarketModel 正确消费了 closing_odds，结果有差异。")
        else:
            print("结论: 结果几乎无差异，可能 closing_odds 与普通 odds 恰好重合。")

    finally:
        db.close()


if __name__ == "__main__":
    main()
