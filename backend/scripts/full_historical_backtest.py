import sys
import os

from database.models import SessionLocal, Team, Match, MatchStatus

def run_global_elo_backtest():
    db = SessionLocal()
    print("🚀 启动全量历史数据对撞 (Global Elo Realignment)...")
    
    # 1. 重置所有球队 Elo 到基准
    db.query(Team).update({Team.elo: 1500})
    db.commit()
    
    # 2. 获取所有已结束比赛（按时间顺序）
    matches = db.query(Match).filter(
        Match.status == MatchStatus.FINISHED,
        Match.actual_outcome.isnot(None)
    ).order_by(Match.kickoff_at.asc()).all()
    
    print(f"  - 正在处理 {len(matches)} 场历史赛事...")
    
    K = 32 # Elo 变动敏感度
    
    for m in matches:
        home = db.query(Team).get(m.home_team_id)
        away = db.query(Team).get(m.away_team_id)
        if not home or not away: continue
        
        # 计算预期得分
        r_h = 10**(home.elo/400)
        r_a = 10**(away.elo/400)
        e_h = r_h / (r_h + r_a)
        e_a = r_a / (r_h + r_a)
        
        # 实际得分
        s_h = 1.0 if m.actual_outcome == 'home' else (0.5 if m.actual_outcome == 'draw' else 0.0)
        s_a = 1.0 - s_h
        
        # 更新 Elo
        home.elo += int(K * (s_h - e_h))
        away.elo += int(K * (s_a - e_a))
        
    db.commit()
    print("✅ 全量 Elo 对撞完成。")
    db.close()

if __name__ == "__main__":
    run_global_elo_backtest()
    # 顺便运行画像生成器
    from scripts.auto_team_stats import calculate_dynamic_stats
    calculate_dynamic_stats()
