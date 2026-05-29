#!/usr/bin/env python3
"""
一键刷新全部比赛赔率。
用法:
    cd /Users/liuxuran/Github/football/backend
    python refresh_odds.py

功能:
    1. 自动迁移数据库（添加 odds_source 列，如不存在）
    2. 为所有比赛采集/刷新赔率
    3. 优先级: BetExplorer 爬虫 > football-data > 合成赔率兜底
"""
import sys
from datetime import datetime

from sqlalchemy import inspect, text

from database.models import init_db, get_db, Match, engine
from odds_collector import OddsCollector


def migrate_odds_source_column():
    """检查并添加 odds_source 列到 matches 表"""
    inspector = inspect(engine)
    columns = [c["name"] for c in inspector.get_columns("matches")]
    if "odds_source" in columns:
        print("✅ odds_source 列已存在，跳过迁移")
        return

    with engine.connect() as conn:
        conn.execute(text("ALTER TABLE matches ADD COLUMN odds_source VARCHAR(20)"))
        conn.commit()
    print("✅ 已添加 odds_source 列到 matches 表")


def refresh_all_odds():
    """刷新所有比赛的赔率"""
    init_db()
    migrate_odds_source_column()

    db = next(get_db())
    matches = db.query(Match).all()
    if not matches:
        print("⚠️ 数据库中没有比赛")
        return

    print(f"\n🚀 开始刷新 {len(matches)} 场比赛的赔率...")
    print("-" * 50)

    collector = OddsCollector(db)
    updated = 0
    synthetic = 0
    failed = 0

    for i, match in enumerate(matches, 1):
        sources = collector.collect_for_match(match)
        if sources:
            collector.update_match_primary_odds(match, sources)
            source_tag = match.odds_source or "unknown"
            if source_tag == "synthetic":
                synthetic += 1
            else:
                updated += 1
            print(
                f"  [{i}/{len(matches)}] {match.match_code:16} "
                f"{match.odds_home:5.2f} / {match.odds_draw:5.2f} / {match.odds_away:5.2f} "
                f"| {source_tag}"
            )
        else:
            failed += 1
            print(f"  [{i}/{len(matches)}] {match.match_code:16} ❌ 无法获取赔率")

    print("-" * 50)
    print(f"\n📊 刷新完成:")
    print(f"   真实赔率: {updated} 场")
    print(f"   合成赔率: {synthetic} 场")
    print(f"   失败:     {failed} 场")
    print(f"   总计:     {len(matches)} 场")

    # 汇总统计
    from sqlalchemy import func
    total = db.query(Match).count()
    with_real = db.query(Match).filter(Match.odds_source != "synthetic").count()
    with_synth = db.query(Match).filter(Match.odds_source == "synthetic").count()
    print(f"\n📈 覆盖率:")
    print(f"   真实赔率: {with_real}/{total} ({with_real/total*100:.1f}%)")
    print(f"   合成赔率: {with_synth}/{total} ({with_synth/total*100:.1f}%)")


if __name__ == "__main__":
    refresh_all_odds()
