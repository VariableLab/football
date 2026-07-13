"""探查数据库结构，确认可用于生成训练数据的字段"""
import sys
import os
from database.models import engine
from sqlalchemy import text

with engine.connect() as conn:
    # 1. 已完成比赛 + 有赔率 + 有结果
    total = conn.execute(text(
        "SELECT COUNT(*) FROM matches WHERE status = 'FINISHED' "
        "AND odds_home IS NOT NULL AND actual_outcome IS NOT NULL"
    )).scalar()
    print(f"有赔率+有结果的已完成比赛: {total}")
    
    # 2. 按赔率来源分布
    print("\n赔率来源分布:")
    rows = conn.execute(text(
        "SELECT odds_source, COUNT(*) FROM matches "
        "WHERE status = 'FINISHED' AND odds_home IS NOT NULL AND actual_outcome IS NOT NULL "
        "GROUP BY odds_source ORDER BY COUNT(*) DESC"
    )).fetchall()
    for r in rows:
        print(f"  {r[0]}: {r[1]}")
    
    # 3. 联赛分布
    print("\n联赛分布（有赔率+结果）:")
    rows = conn.execute(text(
        "SELECT competition, COUNT(*) as cnt FROM matches "
        "WHERE status = 'FINISHED' AND odds_home IS NOT NULL AND actual_outcome IS NOT NULL "
        "GROUP BY competition ORDER BY cnt DESC"
    )).fetchall()
    for r in rows:
        print(f"  {r[0]}: {r[1]}")
    
    # 4. 比赛结果分布
    print("\n比赛结果分布:")
    rows = conn.execute(text(
        "SELECT actual_outcome, COUNT(*) FROM matches "
        "WHERE status = 'FINISHED' AND odds_home IS NOT NULL AND actual_outcome IS NOT NULL "
        "GROUP BY actual_outcome"
    )).fetchall()
    for r in rows:
        print(f"  {r[0]}: {r[1]}")
    
    # 5. 样本行
    print("\n样本数据:")
    row = conn.execute(text(
        "SELECT m.id, m.match_code, m.competition, m.kickoff_at, "
        "m.odds_home, m.odds_draw, m.odds_away, m.odds_source, "
        "m.actual_home_goals, m.actual_away_goals, m.actual_outcome, "
        "ht.name as home_name, ht.elo as home_elo, "
        "at.name as away_name, at.elo as away_elo "
        "FROM matches m "
        "JOIN teams ht ON m.home_team_id = ht.id "
        "JOIN teams at ON m.away_team_id = at.id "
        "WHERE m.status = 'FINISHED' AND m.odds_home IS NOT NULL "
        "AND m.actual_outcome IS NOT NULL "
        "LIMIT 5"
    )).fetchall()
    for r in row:
        print(dict(r._mapping))
    
    # 6. prediction 准确率
    print("\n预测准确率（SPF）:")
    total_preds = conn.execute(text(
        "SELECT COUNT(*) FROM predictions p "
        "JOIN matches m ON p.match_id = m.id "
        "WHERE p.play_type = 'SPF' AND m.status = 'FINISHED' "
        "AND m.actual_outcome IS NOT NULL"
    )).scalar()
    correct = conn.execute(text(
        "SELECT COUNT(*) FROM predictions p "
        "JOIN matches m ON p.match_id = m.id "
        "WHERE p.play_type = 'SPF' AND m.status = 'FINISHED' "
        "AND m.actual_outcome IS NOT NULL AND p.is_correct = 1"
    )).scalar()
    print(f"  {correct}/{total_preds} 正确 ({100*correct/total_preds:.1f}%)" if total_preds else "  无数据")