#!/usr/bin/env python3
"""
综合数据采集脚本

执行流程：
  1. 从 football-data.co.uk 下载历史比赛+赔率
  2. 用 BetExplorer 抓取 upcoming 比赛赔率
  3. 写入 odds_history 表和 Match 表
  4. 输出采集统计报告
"""

import sys
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from database.models import SessionLocal, Match, Team, OddsHistory, MatchStatus
from ingestion.odds_collector import (
    FootballDataSource, BetExplorerSource, OddsCollector, SyntheticOddsSource,
    OddsSnapshot
)
from utils.logger import get_logger

logger = get_logger("collect_data")


def collect_football_data(db: Session):
    """从 football-data 下载历史数据并解析存入缓存"""
    fd = FootballDataSource()
    # 强制刷新缓存
    fd._last_download = None
    fd._cache.clear()
    data = fd.download_all(use_cache=False)
    logger.info(f"[football-data] Downloaded {len(data)} rows")
    return data


def collect_betexplorer_odds(db: Session, matches: list):
    """用 BetExplorer 抓取比赛赔率"""
    be = BetExplorerSource()
    results = []
    for match in matches:
        try:
            snap = be.fetch(match)
            if snap and all(v is not None for v in [snap.odds_home, snap.odds_draw, snap.odds_away]):
                results.append(snap)
                # 写入 odds_history
                history = OddsHistory(
                    match_id=match.id,
                    source="betexplorer",
                    odds_home=snap.odds_home,
                    odds_draw=snap.odds_draw,
                    odds_away=snap.odds_away,
                    recorded_at=datetime.now(timezone.utc),
                    is_closing=False,
                    is_real=True,
                )
                db.add(history)
                # 更新 Match 主赔率
                match.odds_home = snap.odds_home
                match.odds_draw = snap.odds_draw
                match.odds_away = snap.odds_away
                match.odds_source = "betexplorer"
                logger.info(f"[betexplorer] {match.match_code}: {snap.odds_home}/{snap.odds_draw}/{snap.odds_away}")
            else:
                logger.debug(f"[betexplorer] {match.match_code}: no odds found")
        except Exception as e:
            logger.warning(f"[betexplorer] {match.match_code}: {e}")

    if results:
        db.commit()
    return results


def collect_synthetic_odds(db: Session, matches: list):
    """为无真实赔率的比赛生成合成赔率"""
    synth = SyntheticOddsSource()
    snapshots = synth.fetch_batch(matches)
    count = 0
    for snap in snapshots:
        if not snap:
            continue
        match = db.query(Match).filter(Match.id == snap.match_id).first()
        if not match:
            continue
        # 只在没有真实赔率时写入
        has_real = db.query(OddsHistory).filter(
            OddsHistory.match_id == match.id,
            OddsHistory.is_real == True
        ).first()
        if has_real:
            continue

        history = OddsHistory(
            match_id=match.id,
            source="synthetic",
            odds_home=snap.odds_home,
            odds_draw=snap.odds_draw,
            odds_away=snap.odds_away,
            recorded_at=datetime.now(timezone.utc),
            is_closing=False,
            is_real=False,
        )
        db.add(history)
        if match.odds_home is None:
            match.odds_home = snap.odds_home
            match.odds_draw = snap.odds_draw
            match.odds_away = snap.odds_away
            match.odds_source = "synthetic"
        count += 1

    if count:
        db.commit()
    logger.info(f"[synthetic] Generated odds for {count} matches")
    return count


def main():
    import traceback
    db = SessionLocal()
    try:
        print("=" * 60)
        print("综合数据采集脚本")
        print("=" * 60)

        # 1. 查看数据库状态
        total_teams = db.query(Team).count()
        total_matches = db.query(Match).count()
        print(f"\n📊 数据库现状: {total_teams} 支球队, {total_matches} 场比赛")

        # 2. football-data 下载
        print("\n⬇️  Step 1: 下载 football-data 历史数据...")
        try:
            fd_data = collect_football_data(db)
            if fd_data:
                print(f"   ✅ 下载成功: {len(fd_data)} 行")
                sample = fd_data[0]
                print(f"   样例: {sample.get('Date')} {sample.get('HomeTeam')} vs {sample.get('AwayTeam')} "
                      f"| B365: {sample.get('B365H')}/{sample.get('B365D')}/{sample.get('B365A')}")
            else:
                print("   ⚠️  下载失败或为空")
        except Exception as e:
            print(f"   ❌ football-data 错误: {e}")

        # 3. BetExplorer 抓取
        print("\n🔍 Step 2: BetExplorer 抓取赔率...")
        try:
            upcoming = db.query(Match).filter(
                Match.status.in_([MatchStatus.SCHEDULED, MatchStatus.UPCOMING])
            ).all()
            print(f"   待抓取比赛数: {len(upcoming)}")
            be_results = collect_betexplorer_odds(db, upcoming)
            print(f"   ✅ BetExplorer 成功: {len(be_results)} 场")
        except Exception as e:
            print(f"   ❌ BetExplorer 错误: {e}")
            traceback.print_exc()

        # 4. 合成赔率兜底
        print("\n🎲 Step 3: 合成赔率兜底...")
        try:
            unmatched = db.query(Match).filter(
                Match.odds_home.is_(None)
            ).all()
            print(f"   无赔率比赛数: {len(unmatched)}")
            synth_count = collect_synthetic_odds(db, unmatched)
            print(f"   ✅ 合成赔率覆盖: {synth_count} 场")
        except Exception as e:
            print(f"   ❌ 合成赔率错误: {e}")
            traceback.print_exc()

        # 5. 统计报告
        print("\n" + "=" * 60)
        print("📈 采集报告")
        print("=" * 60)

        odds_count = db.query(OddsHistory).count()
        real_odds = db.query(OddsHistory).filter(OddsHistory.is_real == True).count()
        synth_odds = db.query(OddsHistory).filter(OddsHistory.is_real == False).count()

        matches_with_odds = db.query(Match).filter(Match.odds_home.isnot(None)).count()
        matches_with_real = db.query(Match).filter(Match.odds_source != "synthetic").count()

        print(f"   OddsHistory 总记录: {odds_count}")
        print(f"     - 真实赔率: {real_odds}")
        print(f"     - 合成赔率: {synth_odds}")
        print(f"   比赛赔率覆盖: {matches_with_odds}/{total_matches}")
        print(f"   真实赔率覆盖: {matches_with_real}/{total_matches}")

        # 显示几条最新记录
        print(f"\n   最近采集记录:")
        recent = db.query(OddsHistory).order_by(OddsHistory.recorded_at.desc()).limit(5).all()
        for h in recent:
            match = db.query(Match).filter(Match.id == h.match_id).first()
            code = match.match_code if match else "?"
            real_tag = "[真实]" if h.is_real else "[合成]"
            print(f"     {code} {real_tag} {h.source}: {h.odds_home}/{h.odds_draw}/{h.odds_away}")

        print("\n✅ 数据采集完成")

    except Exception as e:
        print(f"\n❌ 脚本异常: {e}")
        traceback.print_exc()
    finally:
        db.close()


if __name__ == "__main__":
    main()
