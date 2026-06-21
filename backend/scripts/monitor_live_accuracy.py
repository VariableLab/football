import sys
import os
from datetime import datetime, timedelta, timezone

# 确保可以导入后端模块
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from database.models import Match, Prediction, MatchStatus, PlayType
from database.config import get_settings

def check_live_performance():
    """
    检查最近 48 小时内已结束比赛的预测准确率。
    专门用于验证‘实验室插件’生效后的表现。
    """
    settings = get_settings()
    engine = create_engine(settings.DATABASE_URL)
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()

    print(f"📊 [{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 赛道实测: 算法性能实时监控...")
    
    try:
        # 1. 查找最近 48 小时结束的比赛
        recent_cutoff = datetime.now(timezone.utc) - timedelta(hours=48)
        matches = db.query(Match).filter(
            Match.status == MatchStatus.FINISHED,
            Match.kickoff_at >= recent_cutoff,
            Match.actual_outcome.isnot(None)
        ).all()

        if not matches:
            print("  - 💡 提示: 最近 48 小时内尚无已结算比赛。")
            return

        total = 0
        hits = 0
        brier_sum = 0.0

        print(f"  - 正在审计 {len(matches)} 场已结算赛事...")

        for m in matches:
            # 查找该场比赛最新的 SPF 预测
            pred = db.query(Prediction).filter(
                Prediction.match_id == m.id,
                Prediction.play_type == PlayType.SPF
            ).order_by(Prediction.locked_at.desc()).first()

            if not pred or not pred.probabilities:
                continue

            probs = pred.probabilities
            outcome = m.actual_outcome # 'home', 'draw', 'away'
            
            # 计算是否命中
            pred_outcome = max(probs, key=probs.get)
            is_hit = (pred_outcome == outcome)
            
            if is_hit: hits += 1
            total += 1

            # 计算 Brier Score (衡量概率质量)
            actual_map = {'home': [1,0,0], 'draw': [0,1,0], 'away': [0,0,1]}
            p_list = [probs.get('home', 0), probs.get('draw', 0), probs.get('away', 0)]
            a_list = actual_map.get(outcome, [0,0,0])
            
            brier = sum((p - a)**2 for p, a in zip(p_list, a_list)) / 3.0
            brier_sum += brier

            # 打印单场细节
            status_icon = "✅" if is_hit else "❌"
            print(f"    {status_icon} {m.home_team.name} vs {m.away_team.name} | 预测: {pred_outcome} | 实际: {outcome} | 信心: {probs.get(pred_outcome, 0):.1%}")

        if total > 0:
            accuracy = hits / total
            avg_brier = brier_sum / total
            print("\n📈 总结报告:")
            print(f"  - 样本量: {total} 场")
            print(f"  - 实时准确率: {accuracy:.1%}")
            print(f"  - 平均 Brier Score: {avg_brier:.4f} (越低越好)")
            
            if accuracy > 0.5:
                print("  - 🚀 状态: 极佳！实验室插件已显著提升性能。")
            elif accuracy > 0.35:
                print("  - 🟢 状态: 正常。优于之前漂移状态。")
            else:
                print("  - ⚠️ 状态: 仍需观察，建议继续优化参数。")
        else:
            print("  - ⚠️ 未发现带有有效预测数据的已结束比赛。")

    finally:
        db.close()

if __name__ == "__main__":
    check_live_performance()
