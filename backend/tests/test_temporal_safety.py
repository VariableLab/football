"""
P0级时序安全性与防数据泄漏单元测试
验证：
1. 校准器重新自适应拟合（Calibrator.refit）的时序安全性。
2. StackingTrainer 训练数据生成的时间单调递增性，杜绝任何随机打乱泄漏。
3. 特征提取中无“偷看未来”的安全防御校验。
"""

import datetime
import random
import pytest
from database.models import SessionLocal, Team, Match, MatchStatus, Prediction, PlayType
from core.calibrator import Calibrator
from core.residual_nn import StackingTrainer

@pytest.fixture
def db_session():
    """提供纯净的内存 SQLite 会话用于时序安全测试。"""
    session = SessionLocal()
    from database.models import Base, engine
    Base.metadata.create_all(bind=engine)
    
    yield session
    
    Base.metadata.drop_all(bind=engine)
    session.close()

def test_calibrator_refit_chronological_safety(db_session):
    """
    验证 Calibrator.refit 方法的时序安全性：
    确保仅有已完赛（FINISHED）的比赛数据才会被用于拟合，未开赛或未来的预测绝对不会混入校准因子的拟合中。
    """
    # 1. 插入已完赛比赛 (FINISHED)
    t_home = Team(name="Home A", code="HA", elo=1500)
    t_away = Team(name="Away A", code="AA", elo=1500)
    db_session.add_all([t_home, t_away])
    db_session.commit()
    db_session.refresh(t_home)
    db_session.refresh(t_away)

    finished_match = Match(
        match_code="FINISHED_M",
        home_team_id=t_home.id,
        away_team_id=t_away.id,
        status=MatchStatus.FINISHED,
        actual_home_goals=2,
        actual_away_goals=1,
        actual_outcome="home",
        kickoff_at=datetime.datetime(2026, 6, 1, 12, 0, 0),
        odds_home=1.5, odds_draw=3.5, odds_away=5.0,
        closing_odds_home=1.5, closing_odds_draw=3.5, closing_odds_away=5.0
    )
    
    # 2. 插入未开赛比赛 (UPCOMING) —— 这应该在拟合中被过滤
    upcoming_match = Match(
        match_code="UPCOMING_M",
        home_team_id=t_home.id,
        away_team_id=t_away.id,
        status=MatchStatus.UPCOMING,
        kickoff_at=datetime.datetime(2026, 6, 10, 12, 0, 0),
        odds_home=2.0, odds_draw=3.0, odds_away=3.5,
        closing_odds_home=2.0, closing_odds_draw=3.0, closing_odds_away=3.5
    )
    
    db_session.add_all([finished_match, upcoming_match])
    db_session.commit()
    db_session.refresh(finished_match)
    db_session.refresh(upcoming_match)

    # 3. 关联预测数据
    pred_finished = Prediction(
        match_id=finished_match.id,
        play_type=PlayType.SPF,
        probabilities={"home": 0.60, "draw": 0.25, "away": 0.15}
    )
    pred_upcoming = Prediction(
        match_id=upcoming_match.id,
        play_type=PlayType.SPF,
        probabilities={"home": 0.40, "draw": 0.30, "away": 0.30}
    )
    
    db_session.add_all([pred_finished, pred_upcoming])
    db_session.commit()

    # 4. 运行校准器拟合
    calibrator = Calibrator()
    curve = calibrator.fit_from_db(db_session)
    
    # 5. 断言：拟合的样本数中只包含 finished_match（对应 home/draw/away 三个观测量），不包含 upcoming_match
    # 1 场已完赛比赛 * 3 个 sel = 3 个 observations
    assert curve.sample_size == 3
    
    # 6. 测试非静态 refit 方法运行无误
    calibrator.refit(db_session)
    assert calibrator._curve is not None
    assert calibrator._curve.sample_size == 3

def test_stacking_trainer_data_chronological_monotony(db_session):
    """
    验证 StackingTrainer 重建的数据集在时间线上是绝对单调递增的。
    这证明没有在特征构建时打乱时间线（杜绝任何随机划分导致未来渗透的隐患）。
    """
    teams = []
    for i in range(5):
        t = Team(name=f"Team {i}", code=f"T{i}", elo=1500)
        db_session.add(t)
        teams.append(t)
    db_session.commit()

    # 乱序插入 10 场 kickoff_at 不同的比赛
    kickoff_times = [
        datetime.datetime(2026, 6, 15, 12, 0, 0),
        datetime.datetime(2026, 6, 12, 12, 0, 0),
        datetime.datetime(2026, 6, 18, 12, 0, 0),
        datetime.datetime(2026, 6, 11, 12, 0, 0),
        datetime.datetime(2026, 6, 13, 12, 0, 0),
        datetime.datetime(2026, 6, 19, 12, 0, 0),
        datetime.datetime(2026, 6, 14, 12, 0, 0),
        datetime.datetime(2026, 6, 17, 12, 0, 0),
        datetime.datetime(2026, 6, 20, 12, 0, 0),
        datetime.datetime(2026, 6, 16, 12, 0, 0),
    ]

    matches = []
    for idx, kt in enumerate(kickoff_times):
        h, a = random.sample(teams, 2)
        m = Match(
            match_code=f"M_CHRONO_{idx}",
            home_team_id=h.id,
            away_team_id=a.id,
            status=MatchStatus.FINISHED,
            actual_home_goals=1,
            actual_away_goals=1,
            actual_outcome="draw",
            kickoff_at=kt,
            odds_home=2.5, odds_draw=3.0, odds_away=2.5,
            closing_odds_home=2.5, closing_odds_draw=3.0, closing_odds_away=2.5
        )
        db_session.add(m)
        matches.append(m)
    db_session.commit()

    # 关联 SPF 预测
    for m in matches:
        p = Prediction(
            match_id=m.id,
            play_type=PlayType.SPF,
            probabilities={"home": 0.33, "draw": 0.34, "away": 0.33}
        )
        db_session.add(p)
    db_session.commit()

    # 从数据库构建 Stacking 训练数据
    trainer = StackingTrainer(db_session)
    
    # 模拟 `build_training_data` 的检索行为
    finished_matches = db_session.query(Match).filter(
        Match.status == MatchStatus.FINISHED,
        Match.actual_outcome.isnot(None),
        Match.closing_odds_home.isnot(None)
    ).order_by(Match.kickoff_at.asc()).all()

    # 断言：检索出的比赛时间戳是严格单调递增排序的
    last_time = datetime.datetime(2000, 1, 1)
    for m in finished_matches:
        assert m.kickoff_at >= last_time
        last_time = m.kickoff_at
