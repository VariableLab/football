import sys, os, logging
sys.path.insert(0, os.path.dirname(__file__))

import logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")

from zgzcw_source import ZgzcwOddsSource, collect_zgzcw_odds
from database.models import SessionLocal, Match, Team

print("=" * 60)
print("TEST 1: 获取比赛索引 + 赔率解析")
print("=" * 60)

source = ZgzcwOddsSource()
try:
    index = source._fetch_match_index(force=True)
    print(f"\n找到 {len(index)} 场比赛:")
    for mid, info in list(index.items())[:10]:
        odds_str = ""
        if info.get("odds_home"):
            odds_str = f"欧赔: {info['odds_home']}/{info['odds_draw']}/{info['odds_away']}"
        if info.get("jcsp_home"):
            odds_str += f" | 竞彩SP: {info['jcsp_home']}/{info['jcsp_draw']}/{info['jcsp_away']}"
        print(f"  [{mid:8d}] {info['time']:20s} {info['status']:4s} {info['league']:12s} {info['home']:16s} vs {info['away']:16s} {odds_str}")

    if not index:
        print("⚠️  今天可能没有比赛，或网站暂时不可用")

    print("\n" + "=" * 60)
    print("TEST 2: 数据库队名匹配测试")
    print("=" * 60)

    db = SessionLocal()
    try:
        # Get upcoming matches
        from datetime import datetime, timedelta
        now = datetime.utcnow()
        upcoming = db.query(Match).filter(
            Match.kickoff_at.between(now, now + timedelta(hours=72))
        ).limit(20).all()

        if upcoming:
            print(f"数据库中有 {len(upcoming)} 场 upcoming 比赛")
            matched = 0
            for m in upcoming:
                home = m.home_team.name if m.home_team else ""
                away = m.away_team.name if m.away_team else ""
                snap = source.fetch(m)
                if snap:
                    matched += 1
                    print(f"  ✓ {m.match_code}: {home} vs {away} → {snap.odds_home}/{snap.odds_draw}/{snap.odds_away}")
                else:
                    print(f"  ✗ {m.match_code}: {home} vs {away} — 今天不是比赛日")
            print(f"\n匹配率: {matched}/{len(upcoming)}")
        else:
            print("数据库中没有 upcoming 比赛")

        print("\n" + "=" * 60)
        print("TEST 3: 完整采集流水线")
        print("=" * 60)
        result = collect_zgzcw_odds(db)
        print(f"结果: {result}")
    finally:
        db.close()
finally:
    source.close()