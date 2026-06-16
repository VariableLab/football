
import os
import sys
import json
from sqlalchemy.orm import Session

# Add backend subdirectories to path
_root = os.path.join(os.getcwd(), 'backend')
for d in ["api", "core", "features", "ingestion", "database", "strategy", "monitor", "utils", "api/routers"]:
    sys.path.append(os.path.join(_root, d))

from database.models import SessionLocal, Match, Team, Prediction, PlayType
from strategy_config import load_params
from edge_calculator import EdgeCalculator
from position_sizer import PositionSizer, RiskTier

def run_ev_scan():
    db = SessionLocal()
    ec = EdgeCalculator()
    params = load_params()
    sizer = PositionSizer(RiskTier.BALANCED)
    bankroll = 1000.0
    
    print(f"\n{'='*60}")
    print(f"  🔍 2026 世界杯外置策略 EV 深度扫描")
    print(f"{'='*60}")
    
    # Get WC2026 matches
    matches = db.query(Match).filter(Match.competition == "WC2026").all()
    
    results = []
    
    for m in matches:
        try:
            # Get SPF prediction
            pred = db.query(Prediction).filter(
                Prediction.match_id == m.id,
                Prediction.play_type == PlayType.SPF
            ).first()
            
            if not pred or not m.odds_home:
                continue
                
            probs = pred.probabilities if isinstance(pred.probabilities, dict) else json.loads(pred.probabilities)
            
            # Market odds
            odds = {
                "home": m.odds_home,
                "draw": m.odds_draw,
                "away": m.odds_away
            }
            
            # 使用 EdgeCalculator 提取精确的 Edge 与 EV (已去 Margin)
            edge_res = ec.compute(odds["home"], odds["draw"], odds["away"], probs)
            
            # 寻找最具有正价值的方向 (EV > 0 且 Edge > 0)
            best_outcome = edge_res.best_selection
            
            # 如果没有任何正价值方向，取 EV 最大的那个方向作为展示，但标记为不推荐下注
            if not best_outcome:
                best_outcome = max(["home", "draw", "away"], key=lambda k: probs.get(k, 0) * odds.get(k, 1.0) - 1.0)
                
            e = edge_res.edges[best_outcome]
            
            # 凯利公式计算
            stake_pct = 0.0
            stake_amount = 0.0
            kelly_raw = 0.0
            if e.is_value:
                stake_res = sizer.compute(e.calibrated_prob, e.odds, bankroll=bankroll)
                stake_pct = stake_res.stake_pct
                stake_amount = stake_res.stake_amount
                kelly_raw = stake_res.kelly_raw
                
            results.append({
                "match": m,
                "best_outcome": best_outcome,
                "ev": e.ev,
                "edge": e.edge,
                "probs": probs,
                "odds": odds,
                "is_value": e.is_value,
                "stake_pct": stake_pct,
                "stake_amount": stake_amount,
                "kelly_raw": kelly_raw
            })
        except Exception as err:
            print(f"❌ Error processing match ID {m.id if m else 'None'} ({m.match_code if m else ''}): {err}")
            import traceback
            traceback.print_exc()
            continue
        
    # Sort by EV descending
    results.sort(key=lambda x: x["ev"], reverse=True)
    
    for r in results:
        m = r["match"]
        home = db.query(Team).filter(Team.id == m.home_team_id).first()
        away = db.query(Team).filter(Team.id == m.away_team_id).first()
        home_name = home.name if home else f"Team_{m.home_team_id}"
        away_name = away.name if away else f"Team_{m.away_team_id}"
        
        if r["is_value"]:
            status_icon = "🔥 VALUE BET"
            stake_info = f"建议凯利下注: {r['stake_pct']:.1%} (${r['stake_amount']:.2f}) | 原始凯利: {r['kelly_raw']:.1%}"
        else:
            status_icon = "⚠️ SKIP (负期望)"
            stake_info = "建议凯利下注: 跳过 (0.00%)"
            
        outcome_label = {"home": "主胜", "draw": "平局", "away": "客胜"}[r["best_outcome"]]
        
        print(f"[{m.match_code}] {home_name} vs {away_name}")
        print(f"  建议方向: {outcome_label} ({r['best_outcome']})")
        print(f"  模型概率: {r['probs'][r['best_outcome']]:.1%}")
        print(f"  市场赔率: {r['odds'][r['best_outcome']]:.2f}")
        print(f"  期望价值 (EV): {r['ev']:+.3f} | 优势 (Edge): {r['edge']:+.1%}")
        print(f"  资金仓位: {stake_info}")
        print(f"  策略状态: {status_icon}")
        print("-" * 30)

    db.close()

if __name__ == "__main__":
    try:
        run_ev_scan()
    except Exception as e:
        print(f"❌ Main level crash: {e}")
        import traceback
        traceback.print_exc()
