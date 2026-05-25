import sys
import os
import json
from datetime import datetime, timedelta, timezone

# Ensure we can import from backend
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from database.models import SessionLocal, Match, MatchStatus, Prediction
from core.prediction_engine import PredictionEngine, build_context_from_match
from quant_backtest import QuantBacktestEngine

def run():
    db = SessionLocal()
    engine = PredictionEngine()
    bt_engine = QuantBacktestEngine(initial_capital=10000.0)
    
    # 1. 选取最近 60 天的竞彩场次数据 (有赛果的)
    sixty_days_ago = datetime.now(timezone.utc) - timedelta(days=60)
    matches = db.query(Match).filter(
        Match.status.ilike("finished"),
        Match.competition.in_(["EPL", "LaLiga", "SerieA", "Bundesliga", "Ligue1"]),
        Match.actual_outcome.isnot(None),
        Match.kickoff_at >= sixty_days_ago
    ).order_by(Match.kickoff_at.asc()).all()
    
    print(f"🚀 开始多维度量化回测 (样本数: {len(matches)})...")
    
    match_data = []
    for m in matches:
        try:
            ctx = build_context_from_match(m)
            res = engine.predict(ctx)
            
            # 选择概率最高的一项作为投注项
            best_sel = max(res.spf, key=res.spf.get)
            prob = res.spf[best_sel]
            
            # 获取对应赔率
            odds = m.odds_home if best_sel == 'home' else (m.odds_away if best_sel == 'away' else m.odds_draw)
            if not odds or odds <= 1.01: continue

            match_data.append({
                'match': f"{m.home_team.name} vs {m.away_team.name}",
                'prob': prob,
                'odds': odds,
                'actual': m.actual_outcome,
                'selection': best_sel,
                'date': m.kickoff_at.isoformat()
            })
        except:
            continue

    # 2. 执行回测 (凯利公式模式)
    bt_engine.run(match_data, stake_rule='kelly')
    metrics = bt_engine.get_metrics()
    
    print("\n" + "="*50)
    print("📈 量化回测核心指标 (60天/五大联赛/1/4 Kelly)")
    print("="*50)
    print(f"💰 初始资金: 10,000.00")
    print(f"📊 累计损益: {metrics.get('total_pnl'):+,}")
    print(f"🚀 ROI (回报率): {metrics.get('roi'):.1%}")
    print(f"🎯 方向准确率: {metrics.get('win_rate'):.1%}")
    print(f"📉 最大回撤: {metrics.get('max_drawdown'):.1%}")
    print(f"💎 夏普比率: {metrics.get('sharpe_ratio')}")
    print(f"📦 测试样本: {metrics.get('sample_size')} 场")
    print("="*50)
    
    db.close()

if __name__ == "__main__":
    run()
