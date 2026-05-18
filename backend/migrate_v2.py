#!/usr/bin/env python3
"""
数据库迁移脚本 v2：添加全维度预测所需字段
用法: python migrate_v2.py
"""
from sqlalchemy import inspect, text
from models import engine


def column_exists(table: str, column: str) -> bool:
    inspector = inspect(engine)
    cols = [c["name"] for c in inspector.get_columns(table)]
    return column in cols


def migrate():
    print("🔄 开始数据库迁移 v2...")

    # ─── teams 表 ───
    team_cols = {
        "recent_results": "VARCHAR(20)",
        "recent_goals_scored": "FLOAT",
        "recent_goals_conceded": "FLOAT",
        "home_away_factor": "FLOAT",
        "weather_adaptability": "FLOAT",
        "tactical_style": "VARCHAR(20)",
        "coach_rating": "FLOAT",
        "rest_days": "INTEGER",
        "key_injuries": "VARCHAR(200)",
    }

    with engine.connect() as conn:
        for col, dtype in team_cols.items():
            if not column_exists("teams", col):
                conn.execute(text(f"ALTER TABLE teams ADD COLUMN {col} {dtype}"))
                print(f"   ✅ teams.{col} ({dtype})")
            else:
                print(f"   ⏭️  teams.{col} 已存在")

        # ─── matches 表 ───
        match_cols = {
            "odds_source": "VARCHAR(20)",
            "venue_type": "VARCHAR(20)",
            "weather": "VARCHAR(20)",
            "temperature": "FLOAT",
            "pitch_condition": "VARCHAR(20)",
            "schedule_density": "VARCHAR(20)",
        }

        for col, dtype in match_cols.items():
            if not column_exists("matches", col):
                conn.execute(text(f"ALTER TABLE matches ADD COLUMN {col} {dtype}"))
                print(f"   ✅ matches.{col} ({dtype})")
            else:
                print(f"   ⏭️  matches.{col} 已存在")

        conn.commit()

    print("\n🎉 迁移完成！")


if __name__ == "__main__":
    migrate()
