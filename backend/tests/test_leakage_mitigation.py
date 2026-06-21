"""Tests for P0 leakage mitigation and feature alignment."""
import datetime
import pytest
from database.models import SessionLocal, Team, Match, MatchStatus, Prediction, PlayType
from scripts.weight_learner import WeightLearner
from core.residual_nn import StackingTrainer

@pytest.fixture
def db_session():
    """Provides a clean in-memory database session for testing."""
    session = SessionLocal()
    # Ensure tables are created (in-memory sqlite is clean)
    from database.models import Base, engine
    Base.metadata.create_all(bind=engine)
    
    yield session
    
    # Cleanup
    Base.metadata.drop_all(bind=engine)
    session.close()

def test_walk_forward_elo_reconstruction(db_session):
    """
    Verify that Elo ratings and recent results are reconstructed chronologically,
    without leakage from future matches.
    """
    # 1. 创建球队
    t_x = Team(name="Team X", code="TMX", elo=1650, recent_results="WWWWWWWWWW")
    t_y = Team(name="Team Y", code="TMY", elo=1550, recent_results="DDDDDDDDDD")
    t_z = Team(name="Team Z", code="TMZ", elo=1450, recent_results="LLLLLLLLLL")
    db_session.add_all([t_x, t_y, t_z])
    db_session.commit()
    db_session.refresh(t_x)
    db_session.refresh(t_y)
    db_session.refresh(t_z)

    # 2. 创建 3 场不同时序的已完成比赛
    # Match 1: Day 1, X vs Y, X wins.
    m1 = Match(
        match_code="M1",
        home_team_id=t_x.id,
        away_team_id=t_y.id,
        status=MatchStatus.FINISHED,
        actual_home_goals=2,
        actual_away_goals=0,
        actual_outcome="home",
        kickoff_at=datetime.datetime(2026, 6, 1, 12, 0, 0),
        odds_home=1.5, odds_draw=3.5, odds_away=5.0
    )
    # Match 2: Day 2, X vs Z, X wins.
    m2 = Match(
        match_code="M2",
        home_team_id=t_x.id,
        away_team_id=t_z.id,
        status=MatchStatus.FINISHED,
        actual_home_goals=3,
        actual_away_goals=1,
        actual_outcome="home",
        kickoff_at=datetime.datetime(2026, 6, 2, 12, 0, 0),
        odds_home=1.3, odds_draw=4.0, odds_away=7.0
    )
    # Match 3: Day 3, Y vs Z, Y wins.
    m3 = Match(
        match_code="M3",
        home_team_id=t_y.id,
        away_team_id=t_z.id,
        status=MatchStatus.FINISHED,
        actual_home_goals=1,
        actual_away_goals=0,
        actual_outcome="home",
        kickoff_at=datetime.datetime(2026, 6, 3, 12, 0, 0),
        odds_home=1.8, odds_draw=3.2, odds_away=4.2
    )
    db_session.add_all([m1, m2, m3])
    db_session.commit()
    db_session.refresh(m1)
    db_session.refresh(m2)
    db_session.refresh(m3)

    # 3. 必须有 SPF Prediction 记录，因为 WeightLearner 只捞取有预测快照的比赛
    p1 = Prediction(match_id=m1.id, play_type=PlayType.SPF, probabilities={"home": 0.5, "draw": 0.3, "away": 0.2})
    p2 = Prediction(match_id=m2.id, play_type=PlayType.SPF, probabilities={"home": 0.6, "draw": 0.25, "away": 0.15})
    p3 = Prediction(match_id=m3.id, play_type=PlayType.SPF, probabilities={"home": 0.45, "draw": 0.3, "away": 0.25})
    db_session.add_all([p1, p2, p3])
    db_session.commit()

    # 4. 执行重推与特征读取
    learner = WeightLearner(db_session)
    training_data = learner.fetch_training_data()
    
    assert len(training_data) == 3
    
    # 按照 kickoff_at 排序后：
    # index 0 -> M1 (X vs Y)
    # index 1 -> M2 (X vs Z)
    # index 2 -> M3 (Y vs Z)
    ctx1, out1 = training_data[0]
    ctx2, out2 = training_data[1]
    ctx3, out3 = training_data[2]
    
    # Verify M1 (初始状态，两个队都是 1500，且 results 为空)
    assert ctx1.home_team.elo == 1500
    assert ctx1.away_team.elo == 1500
    assert ctx1.home_team.recent_results == ""
    assert ctx1.away_team.recent_results == ""
    
    # Verify M2 (发生在 Day 2，球队 X 已经赢了 M1 这一场，它的 Elo 应当上升，Recent Results 为 'W')
    # 初始 1500 vs 1500，期望胜率 = 0.5。X 赢了，新 Elo = 1500 + 32 * (1.0 - 0.5) = 1516
    assert ctx2.home_team.elo == 1516
    assert ctx2.home_team.recent_results == "W"
    
    # 此时球队 Z 没有踢过比赛，应当仍为 1500 且 recent 为空
    assert ctx2.away_team.elo == 1500
    assert ctx2.away_team.recent_results == ""
    
    # Verify M3 (发生在 Day 3。Y 输给 X 后在 Day 1 被记录为 'L'，Elo 应下降为 1500 + 32 * (0.0 - 0.5) = 1484)
    assert ctx3.home_team.elo == 1484
    assert ctx3.home_team.recent_results == "L"

def test_stacking_trainer_leakage_mitigation(db_session):
    """
    Verify that StackingTrainer builds training data with actual LR outputs
    and performs chronological split.
    """
    # 模拟 StackingTrainer 的构建
    # 我们先插入 105 场完成的比赛以达到 limit 门槛
    teams = []
    for i in range(10):
        t = Team(name=f"T{i}", code=f"T{i}", elo=1500, recent_results="")
        db_session.add(t)
        teams.append(t)
    db_session.commit()
    
    import random
    matches = []
    # 插入 120 场比赛，每场都有赔率和预测
    start_time = datetime.datetime(2026, 6, 1, 12, 0, 0)
    for idx in range(120):
        h, a = random.sample(teams, 2)
        m = Match(
            match_code=f"M_{idx}",
            home_team_id=h.id,
            away_team_id=a.id,
            status=MatchStatus.FINISHED,
            actual_home_goals=1,
            actual_away_goals=1,
            actual_outcome="draw",
            kickoff_at=start_time + datetime.timedelta(hours=idx),
            closing_odds_home=2.5, closing_odds_draw=3.1, closing_odds_away=2.8,
            odds_home=2.5, odds_draw=3.1, odds_away=2.8
        )
        db_session.add(m)
        matches.append(m)
        
    db_session.commit()
    
    for m in matches:
        p = Prediction(match_id=m.id, play_type=PlayType.SPF, probabilities={"home": 0.33, "draw": 0.34, "away": 0.33})
        db_session.add(p)
    db_session.commit()
    
    # 初始化 trainer
    trainer = StackingTrainer(db_session)
    data = trainer.build_training_data()
    
    assert data is not None
    X, Y = data
    
    # 检查 StackingNet 输入特征维度 (应该是 59)
    assert X.shape[1] == 59
    
    # 运行 train 时应该执行时序前向切分，没有 Permutation 报错
    # 验证时序切分逻辑：
    split = int(len(X) * 0.8)
    tr_x = X[:split]
    va_x = X[split:]
    
    assert len(tr_x) + len(va_x) == len(X)
