"""
验证预测引擎是否正确消费了 xG / possession 等新字段。

对比实验：
- A: 使用真实 avg_xg（已填充）
- B: 强制 avg_xg=0（回退到旧逻辑 avg_goals_scored）

如果 PoissonModel 确实优先读取 xG，两组结果应有可观测差异。
"""

from core.prediction_engine import (
    PredictionEngine, MatchContext, TeamContext, build_context_from_match,
)
from database.models import SessionLocal, Match


def make_ctx_with_zero_xg(match):
    """构建 MatchContext，但强制 xG/xGA/possession 为 0（模拟旧逻辑）。"""
    ctx = build_context_from_match(match)
    # 旧逻辑：清空高级统计
    ctx.home_team.avg_xg = 0.0
    ctx.home_team.avg_xga = 0.0
    ctx.home_team.possession = 0.0
    ctx.away_team.avg_xg = 0.0
    ctx.away_team.avg_xga = 0.0
    ctx.away_team.possession = 0.0
    return ctx


def print_result(label, result):
    spf = result.spf
    print(f"\n=== {label} ===")
    print(f"  SPF: 主={spf.get('home', 0):.2%} 平={spf.get('draw', 0):.2%} 客={spf.get('away', 0):.2%}")
    # 取 score 中概率最高的比分
    score_items = sorted(result.score.items(), key=lambda x: x[1], reverse=True)
    most_likely = score_items[0] if score_items else ("N/A", 0)
    print(f"  最可能比分: {most_likely[0]} ({most_likely[1]:.2%})")


def verify():
    db = SessionLocal()
    try:
        # 选一场 xG 与 avg_goals 差异较大的比赛验证（德国 vs 哥伦比亚）
        match = db.query(Match).filter(
            Match.home_team.has(name='德国')
        ).first()
        if not match:
            match = db.query(Match).filter(Match.id == 1).first()
        if not match:
            print("No match found with id=1")
            return

        home = match.home_team
        away = match.away_team
        print(f"比赛: {home.name} vs {away.name}")
        print(f"主队: elo={home.elo}, avg_goals={home.avg_goals_scored}, avg_xg={home.avg_xg}, possession={home.possession}")
        print(f"客队: elo={away.elo}, avg_goals={away.avg_goals_scored}, avg_xg={away.avg_xg}, possession={away.possession}")

        engine = PredictionEngine(db_session=db)

        # A. 新逻辑（使用 xG）
        ctx_new = build_context_from_match(match)
        result_new = engine.predict(ctx_new)
        print_result("新逻辑（使用 xG/possession）", result_new)

        # B. 旧逻辑（xG=0，回退到 avg_goals）
        ctx_old = make_ctx_with_zero_xg(match)
        result_old = engine.predict(ctx_old)
        print_result("旧逻辑（xG=0，回退到 avg_goals）", result_old)

        # 差异判定
        diff_home = abs(result_new.spf.get("home", 0) - result_old.spf.get("home", 0))
        diff_draw = abs(result_new.spf.get("draw", 0) - result_old.spf.get("draw", 0))
        diff_away = abs(result_new.spf.get("away", 0) - result_old.spf.get("away", 0))

        print(f"\n=== 差异 ===")
        print(f"  主胜概率差: {diff_home:.2%}")
        print(f"  平局概率差: {diff_draw:.2%}")
        print(f"  客胜概率差: {diff_away:.2%}")

        if diff_home > 0.001 or diff_draw > 0.001 or diff_away > 0.001:
            print("\n结论: 新字段确实被预测引擎消费，结果有差异。")
        else:
            print("\n结论: 结果几乎无差异，可能 xG 与 avg_goals 恰好重合。")

    finally:
        db.close()


if __name__ == "__main__":
    verify()
