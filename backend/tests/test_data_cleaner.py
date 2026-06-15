"""Tests for DataCleaner, including SQLite compatibility and player stats cascades."""
import datetime
import pytest
from database.models import SessionLocal, Team, Match, MatchStatus, OddsHistory, PlayerStats, Base, engine
from ingestion.data_cleaner import DataCleaner, resolve_team_db, resolve_team_name

@pytest.fixture
def db_session():
    """Provides a clean in-memory database session for testing."""
    session = SessionLocal()
    Base.metadata.create_all(bind=engine)
    
    yield session
    
    Base.metadata.drop_all(bind=engine)
    session.close()

def test_resolve_team_db_limits_three_chars(db_session):
    """
    Verify that resolve_team_db matches Team.code only when raw name length is exactly 3.
    """
    # 建立国家队 Angola
    angola = Team(name="Angola", code="ANG")
    db_session.add(angola)
    db_session.commit()
    db_session.refresh(angola)
    
    # 1. 精确 3 字符 "ANG" 应该对齐成功
    match_id_ang = resolve_team_db(db_session, "ANG")
    assert match_id_ang == angola.id
    
    # 2. 超过 3 字符但前缀相撞的 "ANGOLA_MOCK" 应该对齐失败 (返回 None)
    match_id_long = resolve_team_db(db_session, "ANGOLA_MOCK")
    assert match_id_long is None

def test_sqlite_odds_deduplication(db_session):
    """
    Verify that duplicate OddsHistory records within 5min window are cleaned in SQLite.
    """
    t1 = Team(name="T1", code="TT1")
    t2 = Team(name="T2", code="TT2")
    db_session.add_all([t1, t2])
    db_session.commit()
    db_session.refresh(t1)
    db_session.refresh(t2)
    
    m = Match(
        match_code="M_TEST",
        home_team_id=t1.id,
        away_team_id=t2.id,
        status=MatchStatus.FINISHED,
        kickoff_at=datetime.datetime(2026, 6, 15, 12, 0, 0)
    )
    db_session.add(m)
    db_session.commit()
    db_session.refresh(m)
    
    # 在 5min 窗口内创建两条赔率重复行
    t_base = datetime.datetime(2026, 6, 15, 10, 0, 0)
    oh1 = OddsHistory(match_id=m.id, source="odds-api", odds_home=2.0, odds_draw=3.0, odds_away=4.0, recorded_at=t_base)
    oh2 = OddsHistory(match_id=m.id, source="odds-api", odds_home=2.05, odds_draw=3.0, odds_away=4.0, recorded_at=t_base + datetime.timedelta(minutes=2))
    
    db_session.add_all([oh1, oh2])
    db_session.commit()
    
    # 确认初始状态有两条历史赔率
    assert db_session.query(OddsHistory).filter(OddsHistory.match_id == m.id).count() == 2
    
    # 运行数据清洗，确认在 SQLite 模式下赔率去重被执行且通过
    cleaner = DataCleaner(db_session)
    result = cleaner.clean(dry_run=False)
    
    assert result.fixed.get("odds_dedup", 0) == 1
    # 应该剩下一条（最新的一条，即 oh2）
    remaining = db_session.query(OddsHistory).filter(OddsHistory.match_id == m.id).all()
    assert len(remaining) == 1
    assert remaining[0].odds_home == 2.05

def test_team_merge_cascades_player_stats(db_session, monkeypatch):
    """
    Verify that merging duplicate teams transfers PlayerStats to primary team
    and prevents foreign key crash when dupe team is deleted.
    """
    # mock aliases yaml 映射库，方便测试
    test_aliases = {"Germany": "德国"}
    monkeypatch.setattr("ingestion.data_cleaner.TEAM_ALIASES", test_aliases)
    
    primary = Team(name="德国", code="GER")
    dupe = Team(name="Germany", code="DEU") # 别名重复队
    db_session.add_all([primary, dupe])
    db_session.commit()
    db_session.refresh(primary)
    db_session.refresh(dupe)
    
    # 给别名队伍创建关联的 PlayerStats 记录
    player = PlayerStats(team_id=dupe.id, player_name="Thomas Müller", season="2026")
    db_session.add(player)
    db_session.commit()
    db_session.refresh(player)
    
    # 执行清洗，合并 Germany 队到 德国 队中
    cleaner = DataCleaner(db_session)
    result = cleaner.clean(dry_run=False)
    
    assert result.fixed.get("teams_merged", 0) == 1
    
    # 验证别名队 dupe 已被成功物理删除
    assert db_session.query(Team).filter(Team.id == dupe.id).first() is None
    
    # 验证 PlayerStats 并没有报错回滚，而是被成功转移到了 primary.id
    db_session.refresh(player)
    assert player.team_id == primary.id
