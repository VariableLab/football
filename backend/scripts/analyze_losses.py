"""
回测亏损分析脚本 (Analyze Backtest Losses)
用法: python3 scripts/analyze_losses.py
"""
import sys
import os

import logging
from database.config import get_settings
from database.models import get_db, Match, MatchStatus
from core.prediction_engine import PredictionEngine, build_context_from_match
from strategy.ev_maximizing_strategy import EVMaximizingStrategy

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("analyze_losses")

def analyze():
    db = next(get_db())
    engine = PredictionEngine(db_session=db)
    
    finished_matches = db.query(Match).filter(
        Match.status == MatchStatus.FINISHED,
        Match.actual_outcome.isnot(None),
        Match.closing_odds_home.isnot(None),
        Match.closing_odds_draw.isnot(None),
        Match.closing_odds_away.isnot(None)
    ).order_by(Match.kickoff_at.desc()).limit(150).all()
    
    losses = []
    total_recs = 0
    total_loss_stake = 0.0

    for m in finished_matches:
        try:
            ctx = build_context_from_match(m)
            res = engine.predict(ctx)
            predictions = [
                {"play_type": "SPF", "probabilities": res.spf},
                {"play_type": "SCORE", "probabilities": res.score},
                {"play_type": "HALF", "probabilities": res.half},
            ]
            strategy = EVMaximizingStrategy(
                predictions, m.closing_odds_home, m.closing_odds_draw, m.closing_odds_away, 0.0
            )
            recs = strategy.generate(min_ev=0.005)
            if not recs:
                continue

            best_portfolio = recs[0]
            total_recs += 1
            kelly_pct = best_portfolio.kelly_fraction if best_portfolio.kelly_fraction > 0 else 0.02

            actual = m.actual_outcome
            actual_score = f"{m.actual_home_goals}:{m.actual_away_goals}"

            net_payout = 0.0
            for leg in best_portfolio.legs:
                stake = kelly_pct * leg.stake_pct
                is_hit = False
                if leg.play == "SPF":
                    is_hit = (leg.selection == actual)
                elif leg.play == "SCORE":
                    is_hit = (leg.selection == actual_score)

                if is_hit:
                    net_payout += stake * leg.odds

            net_profit = net_payout - kelly_pct
            
            # 如果亏损（净利润为负）
            if net_profit < -1e-5:
                total_loss_stake += kelly_pct
                # 获取对冲盲区（即没有被选中的那个SPF选项）
                selected_selections = [leg.selection for leg in best_portfolio.legs]
                losses.append({
                    "match_id": m.id,
                    "league": m.competition,
                    "home": m.home_team.name,
                    "away": m.away_team.name,
                    "actual": actual,
                    "actual_score": actual_score,
                    "strat_type": best_portfolio.strategy_type,
                    "name": best_portfolio.name,
                    "legs": [
                        {
                            "type": leg.leg_type,
                            "selection": leg.selection,
                            "prob": leg.probability,
                            "odds": leg.odds,
                            "stake_pct": leg.stake_pct
                        }
                        for leg in best_portfolio.legs
                    ],
                    "kelly_stake": kelly_pct,
                    "net_profit": net_profit,
                    "blind_spot_outcome": actual
                })

        except Exception as e:
            continue

    print(f"\n=== 共发现 {len(losses)} 场亏损场次 (占推荐场次的 {len(losses)/total_recs:.1%}) ===")
    
    # 统计亏损的玩法分布
    strat_dist = {}
    blind_spot_dist = {}
    for l in losses:
        strat_dist[l["strat_type"]] = strat_dist.get(l["strat_type"], 0) + 1
        
        # 找出具体是在什么结果上亏损的
        blind_spot_dist[l["actual"]] = blind_spot_dist.get(l["actual"], 0) + 1

    print("\n1. 亏损策略分布:")
    for k, v in strat_dist.items():
        print(f"   - {k}: {v} 次")

    print("\n2. 亏损时的实际赛果 (对冲盲区击穿) 分布:")
    for k, v in blind_spot_dist.items():
        print(f"   - {k.upper()}: {v} 次")

    print("\n3. 典型大额亏损案例分析:")
    sorted_losses = sorted(losses, key=lambda x: x["net_profit"])
    for l in sorted_losses[:5]:
        print(f"   - Match {l['match_id']} [{l['league']}] {l['home']} vs {l['away']}")
        print(f"     实际赛果: {l['actual']} (比分 {l['actual_score']}) | 策略: {l['name']}")
        print(f"     投注项明细:")
        for leg in l["legs"]:
            print(f"       * {leg['type'].upper()} ({leg['selection']}): 概率={leg['prob']:.1%}, 赔率={leg['odds']:.2f}, 仓位={leg['stake_pct']:.1%}")
        print(f"     损失金额: {l['net_profit']:.4f} (投注本金={l['kelly_stake']:.1%})")
        print("-" * 40)

if __name__ == "__main__":
    analyze()
