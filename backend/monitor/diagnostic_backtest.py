import sys
import os
import json
from datetime import datetime, timedelta, timezone

# Ensure we can import from backend
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from database.models import Match, Prediction, MatchStatus
from database.config import get_settings

def run_diagnostic():
    settings = get_settings()
    engine_db = create_engine(settings.DATABASE_URL)
    SessionLocal = sessionmaker(bind=engine_db)
    db = SessionLocal()

    try:
        # Find finished matches in the last 30 days that have actual outcomes
        thirty_days_ago = datetime.now(timezone.utc) - timedelta(days=30)
        
        matches = db.query(Match).filter(
            Match.status == MatchStatus.FINISHED,
            Match.kickoff_at >= thirty_days_ago,
            Match.actual_outcome.isnot(None)
        ).all()

        print(f"找到 {len(matches)} 场近期结束的比赛用于诊断。")

        if not matches:
            print("数据库中没有近期的已结束比赛数据。")
            return

        total_matches = 0
        correct_predictions = 0
        
        # 错题本分类
        league_stats = {} # {league: {"total": 0, "correct": 0}}
        mistakes_by_odds_range = {"heavy_fav_failed": 0, "underdog_missed": 0, "draw_missed": 0}
        brier_sum = 0.0
        pred_counts = {"home": 0, "draw": 0, "away": 0}
        conf_sums = {"home": 0.0, "draw": 0.0, "away": 0.0}

        from prediction_engine import PredictionEngine, build_context_from_match
        engine = PredictionEngine()
        high_edge_mistakes = []

        for m in matches:
            # 使用最新的引擎重新预测
            try:
                ctx = build_context_from_match(m)
                res = engine.predict(ctx)
                probs = res.spf
                
                # 计算 Edge (如果有市场赔率)
                max_edge = 0.0
                if res.raw_market:
                    edge_h = probs.get("home", 0) - res.raw_market.get("home", 0)
                    edge_d = probs.get("draw", 0) - res.raw_market.get("draw", 0)
                    edge_a = probs.get("away", 0) - res.raw_market.get("away", 0)
                    max_edge = max(edge_h, edge_d, edge_a)
            except Exception:
                continue

            total_matches += 1
            pred_outcome = max(["home", "draw", "away"], key=lambda k: probs.get(k, 0))
            pred_counts[pred_outcome] += 1
            conf_sums[pred_outcome] += probs.get(pred_outcome, 0)
            is_correct = (pred_outcome == m.actual_outcome)
            
            if not is_correct and max_edge > 0.10:
                high_edge_mistakes.append({
                    "match": f"{m.home_team.name} vs {m.away_team.name}",
                    "pred": pred_outcome,
                    "actual": m.actual_outcome,
                    "edge": max_edge
                })

            if is_correct:
                correct_predictions += 1
            
            # 联赛统计
            league = m.competition or "Unknown"
            if league not in league_stats:
                league_stats[league] = {"total": 0, "correct": 0}
            league_stats[league]["total"] += 1
            if is_correct:
                league_stats[league]["correct"] += 1
                
            # 计算 Brier
            actual_array = [1 if m.actual_outcome == 'home' else 0,
                            1 if m.actual_outcome == 'draw' else 0,
                            1 if m.actual_outcome == 'away' else 0]
            pred_array = [probs.get('home', 0), probs.get('draw', 0), probs.get('away', 0)]
            
            brier = sum((p - a)**2 for p, a in zip(pred_array, actual_array)) / 3.0
            brier_sum += brier

            # 错题分析
            if not is_correct:
                max_prob = probs.get(pred_outcome, 0)
                if max_prob > 0.6 and m.actual_outcome != pred_outcome:
                    mistakes_by_odds_range["heavy_fav_failed"] += 1
                if m.actual_outcome == 'draw' and pred_outcome != 'draw':
                    mistakes_by_odds_range["draw_missed"] += 1

        if total_matches == 0:
            print("没有找到带有 SPF 预测数据的已结束比赛。")
            return

        accuracy = correct_predictions / total_matches
        avg_brier = brier_sum / total_matches

        print("\n" + "="*50)
        print("📊 赛前模型诊断报告 (最近30天)")
        print("="*50)
        print(f"总评估场次: {total_matches}")
        print(f"总体方向准确率: {accuracy:.1%}")
        print(f"总体 Brier 分数: {avg_brier:.4f} (越低越好)")
        
        print("\n⚖️ 预测分布与平均信心:")
        for k in ["home", "draw", "away"]:
            v = pred_counts[k]
            avg_conf = conf_sums[k] / v if v > 0 else 0
            print(f"  - {k:6}: {v:3} 场 ({v/total_matches:5.1%}) | 平均信心: {avg_conf:5.1%}")
        
        print("\n🔍 错题本分析 (模型盲区):")
        print(f"- 强队爆冷(模型概率>60%但没赢)次数: {mistakes_by_odds_range['heavy_fav_failed']}")
        print(f"- 漏判的平局次数: {mistakes_by_odds_range['draw_missed']}")
        
        print("\n📉 联赛表现排行 (准确率):")
        sorted_leagues = sorted(league_stats.items(), key=lambda x: x[1]["correct"]/x[1]["total"], reverse=True)
        for l, stats in sorted_leagues[:10]:
            acc = stats["correct"] / stats["total"]
            print(f"  - {l:20}: {acc:6.1%} ({stats['correct']}/{stats['total']})")
            
        if high_edge_mistakes:
            print("\n🚩 高 Edge 失败案例 (模型过度自信):")
            for m_item in high_edge_mistakes[:5]:
                print(f"  - {m_item['match']}: 预测={m_item['pred']}, 实际={m_item['actual']} (Edge={m_item['edge']:.1%})")

    except Exception as e:
        print(f"Error during diagnostic: {e}")
        import traceback
        traceback.print_exc()
    finally:
        db.close()

if __name__ == "__main__":
    run_diagnostic()
