#!/usr/bin/env python3
"""
从 manual_form.json 手动导入近期战绩到数据库

用法：
    python import_manual_form.py
"""
import json
from pathlib import Path

from database.models import SessionLocal, Team
from prediction_engine import FormAdjustmentModel
from form_collector import InternalFormSource


def main():
    db = SessionLocal()
    try:
        path = Path(__file__).parent / "manual_form.json"
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        updated = 0
        for code, info in data.items():
            if code.startswith("_"):
                continue

            team = db.query(Team).filter(Team.code == code).first()
            if not team:
                print(f"⚠️  跳过: 数据库中无球队 {code}")
                continue

            results = info.get("recent_results", "")
            team.recent_results = results
            team.recent_goals_scored = info.get("recent_goals_scored", 0.0)
            team.recent_goals_conceded = info.get("recent_goals_conceded", 0.0)
            team.form_factor = InternalFormSource._compute_form_factor(results)

            print(
                f"✅ [{code}] {team.name:<12} | "
                f"{results} | 状态{team.form_factor:.2f}"
            )
            updated += 1

        db.commit()
        print(f"\n📈 共导入 {updated} 支球队近期战绩")
        print("   现在可以运行: python3 backtest_2022_wc.py")
    finally:
        db.close()


if __name__ == "__main__":
    main()
