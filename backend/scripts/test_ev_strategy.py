"""
期望价值 (Positive EV) 最大化对冲策略仿真回测脚本
用法: python3 scripts/test_ev_strategy.py
"""
import sys
import os

import logging
from database.config import get_settings
from database.models import get_db, Match, MatchStatus
from core.prediction_engine import PredictionEngine, build_context_from_match
from strategy.ev_maximizing_strategy import EVMaximizingStrategy

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("test_ev_strategy")

def run_simulation(limit=200):
    db = next(get_db())
    engine = PredictionEngine(db_session=db)
    
    # 查找有完整赔率和实际赛果的历史完成比赛
    finished_matches = db.query(Match).filter(
        Match.status == MatchStatus.FINISHED,
        Match.actual_outcome.isnot(None),
        Match.closing_odds_home.isnot(None),
        Match.closing_odds_draw.isnot(None),
        Match.closing_odds_away.isnot(None)
    ).order_by(Match.kickoff_at.desc()).limit(limit).all()
    
    if not finished_matches:
        logger.error("No finished matches found for simulation!")
        return

    logger.info(f"Loaded {len(finished_matches)} finished matches for EV strategy backtest...")

    total_stake = 0.0
    total_net_profit = 0.0
    wins = 0
    total_recommendations = 0

    for m in finished_matches:
        try:
            ctx = build_context_from_match(m)
            # 计算模型的全维度预测
            res = engine.predict(ctx)
            
            predictions = [
                {"play_type": "SPF", "probabilities": res.spf},
                {"play_type": "SCORE", "probabilities": res.score},
                {"play_type": "HALF", "probabilities": res.half},
            ]
            collapse_prob = getattr(res, "collapse_prob", 0.0)

            # 初始化我们的 EV 策略生成器
            strategy = EVMaximizingStrategy(
                match_predictions=predictions,
                odds_home=m.closing_odds_home,
                odds_draw=m.closing_odds_draw,
                odds_away=m.closing_odds_away,
                collapse_prob=collapse_prob,
                home_team_name=m.home_team.name if m.home_team else "",
                away_team_name=m.away_team.name if m.away_team else ""
            )
            
            recommendations = strategy.generate(min_ev=0.005)
            if not recommendations:
                for outcome, prob in res.spf.items():
                    odds_val = getattr(m, f"closing_odds_{outcome}", 1.0) or 1.0
                    ev = prob * odds_val - 1.0
                    if ev > 0.0:
                        logger.debug(f"Match {m.id}: Outcome {outcome} has positive EV: {ev:+.2f} (prob={prob:.1%}, odds={odds_val:.2f})")
                continue

            # 选择 EV 最高的投资组合
            best_portfolio = recommendations[0]
            total_recommendations += 1

            # 凯利仓位
            kelly_pct = best_portfolio.kelly_fraction
            if kelly_pct <= 0:
                kelly_pct = 0.02  # 默认最小仓位

            # 模拟执行
            actual = m.actual_outcome  # "home", "draw", "away", "abandoned"
            actual_score = f"{m.actual_home_goals}:{m.actual_away_goals}"

            if actual == "abandoned":
                net_profit = 0.0
                total_stake += kelly_pct
            else:
                net_payout = 0.0
                # 仓位占比在各个 leg 中进行了 dutching 分摊
                for leg in best_portfolio.legs:
                    stake = kelly_pct * leg.stake_pct
                    total_stake += stake

                    # 判断该 leg 是否打出
                    is_hit = False
                    if leg.play == "SPF":
                        is_hit = (leg.selection == actual)
                    elif leg.play == "SCORE":
                        is_hit = (leg.selection == actual_score)

                    if is_hit:
                        payout = stake * leg.odds
                        net_payout += payout

                net_profit = net_payout - kelly_pct
            total_net_profit += net_profit
            
            if net_profit > 0:
                wins += 1

            logger.info(
                f"Match {m.id} ({m.home_team.name} vs {m.away_team.name}): "
                f"Strat={best_portfolio.strategy_type} | "
                f"Stake={kelly_pct:.1%} | Net Profit={net_profit:+.4f} | "
                f"Combined Win Prob={best_portfolio.win_prob_combined:.1%}"
            )
        except Exception as e:
            logger.error(f"Error processing match {m.id}: {e}", exc_info=True)
            continue

    if total_stake > 0:
        overall_roi = total_net_profit / total_stake
        win_rate = wins / total_recommendations if total_recommendations > 0 else 0
        logger.info("=" * 60)
        logger.info(f"期望价值对冲策略仿真回测结果 ({limit} 场赛程)：")
        logger.info(f"  建议投注次数: {total_recommendations}")
        logger.info(f"  总投注本金: {total_stake:.4f}")
        logger.info(f"  总净利润: {total_net_profit:+.4f}")
        logger.info(f"  策略回报率 (ROI): {overall_roi*100:.2f}%")
        logger.info(f"  投资组合胜率 (有盈利的占比): {win_rate*100:.1f}%")
        logger.info("=" * 60)
    else:
        logger.info("No recommendations made during the simulation period.")

    db.close()

if __name__ == "__main__":
    run_simulation(150)
