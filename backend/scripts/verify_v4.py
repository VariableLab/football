import argparse
from sqlalchemy.orm import Session
from database.models import SessionLocal, Match, MatchStatus
from core.prediction_engine import PredictionEngine, build_context_from_match
import datetime

def main():
    db = SessionLocal()
    # 随机选几场最近已完场，或者即将开始的焦点战
    matches = (
        db.query(Match)
        .filter(Match.status == MatchStatus.SCHEDULED)
        .order_by(Match.kickoff_at.asc())
        .limit(10)
        .all()
    )
    
    if not matches:
        matches = db.query(Match).order_by(Match.kickoff_at.desc()).limit(10).all()
        
    engine = PredictionEngine(db_session=db)
    
    print("=" * 60)
    print(f"    v4.0 Deep Frontier 预测引擎验收报告 (抽样 {len(matches)} 场)")
    print("=" * 60)
    
    for m in matches:
        ctx = build_context_from_match(m)
        if not ctx:
            continue
        res = engine.predict(ctx)
        
        # 提取胜平负概率
        spf = res.spf
        h_prob = spf.get("home", 0) * 100
        d_prob = spf.get("draw", 0) * 100
        a_prob = spf.get("away", 0) * 100
        
        # 提取比分推荐
        score_preds = res.score
        # 取 top 3 比分
        top_scores = []
        if score_preds:
            sorted_scores = sorted(score_preds.items(), key=lambda x: x[1], reverse=True)[:3]
            top_scores = [f"{k} ({v*100:.1f}%)" for k, v in sorted_scores]
        
        print(f"\n[比赛] {m.competition} | {m.home_team.name} vs {m.away_team.name}")
        print(f"  > 开赛时间: {m.kickoff_at}")
        print(f"  > [SPF 预测] 胜: {h_prob:.1f}% | 平: {d_prob:.1f}% | 负: {a_prob:.1f}%")
        print(f"  > [比分推荐] {', '.join(top_scores)}")
        
        # 简单判定倾向
        if h_prob > max(d_prob, a_prob):
            print("  > [AI 倾向] 主队优势 🔼")
        elif a_prob > max(d_prob, h_prob):
            print("  > [AI 倾向] 客队优势 🔽")
        else:
            print("  > [AI 倾向] 胶着/易平 ⚖️")

if __name__ == "__main__":
    main()
