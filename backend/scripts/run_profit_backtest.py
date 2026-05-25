import sys
import os
import json
import numpy as np
from datetime import datetime, timedelta, timezone

# Ensure we can import from backend
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from database.models import SessionLocal, Match, MatchStatus
from core.prediction_engine import PredictionEngine, build_context_from_match
from quant_backtest import QuantBacktestEngine

def run():
    db = SessionLocal()
    # 强制启用神经网络修正
    engine = PredictionEngine(use_lr_fusion=True)
    bt_engine = QuantBacktestEngine(initial_capital=10000.0)
    
    # 选取最近 200 场比赛进行“利润导向”回测
    matches = db.query(Match).filter(
        Match.actual_outcome.isnot(None),
        Match.odds_home.isnot(None)
    ).order_by(Match.kickoff_at.desc()).limit(200).all()
    
    print(f"🚀 开始利润导向回测 (200 场最新样本)...")
    
    match_data = []
    for m in matches:
        try:
            ctx = build_context_from_match(m)
            res = engine.predict(ctx)
            
            # 策略：选择具有最高 Edge 的选项 (量化博弈核心)
            best_sel = max(res.spf, key=lambda k: res.spf[k] - (1.0/m.odds_home if k=='home' else (1.0/m.odds_away if k=='away' else 1.0/m.odds_draw)))
            prob = res.spf[best_sel]
            odds = m.odds_home if best_sel == 'home' else (m.odds_away if best_sel == 'away' else m.odds_draw)

            match_data.append({
                'match': f"{m.home_team.name} vs {m.away_team.name}",
                'prob': prob,
                'odds': odds,
                'actual': m.actual_outcome,
                'selection': best_sel
            })
        except:
            continue

    # 执行回测
    bt_engine.run(match_data, stake_rule='kelly')
    metrics = bt_engine.get_metrics()
    
    print("\n" + "="*50)
    print("💎 利润导向模型 (v3.5 + NN Residual) 实战表现")
    print("="*50)
    print(f"💰 初始资金: 10,000.00")
    print(f"📊 累计损益: {metrics.get('total_pnl'):+,}")
    print(f"🚀 ROI (回报率): {metrics.get('roi'):.1%}")
    print(f"🎯 方向准确率: {metrics.get('win_rate'):.1%}")
    print(f"📉 最大回撤: {metrics.get('max_drawdown'):.1%}")
    print(f"💎 夏普比率: {metrics.get('sharpe_ratio')}")
    print("="*50)
    
    db.close()

if __name__ == "__main__":
    os.environ["SECRET_KEY"] = "temp-secret-key-at-least-32-chars-long"
    os.environ["ADMIN_API_KEY"] = "temp-admin-key-long-enough"
    run()
