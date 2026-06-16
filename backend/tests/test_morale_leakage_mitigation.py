"""Tests for news morale leakage mitigation and JSON snapshot extraction in historical backtest."""
import datetime
import pytest
from database.models import SessionLocal, Team, Match, MatchStatus, Prediction, PlayType, MatchAIReport
from scripts.weight_learner import WeightLearner

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

def test_morale_leakage_mitigation_and_fallback(db_session):
    """
    Test two key features of the morale leakage mitigation:
    1. Chronological feature reconstruction falls back to computing form_factor 
       from historical recent results when no AI report is available, ignoring the current team form_factor.
    2. Chronological feature reconstruction extracts historical form_factor and key_injuries
       from the JSON_SNAPSHOT in the MatchAIReport if available, ignoring the current team values.
    """
    # 1. Create teams
    # Set their current form_factor and key_injuries to post-event "future" values.
    t_a = Team(
        name="Team A",
        code="TMA",
        elo=1600,
        form_factor=1.50,          # Current / Future value
        key_injuries="FutureInjuryA", # Current / Future injury
        recent_results="WWWWWWWWWW"
    )
    t_b = Team(
        name="Team B",
        code="TMB",
        elo=1500,
        form_factor=0.60,          # Current / Future value
        key_injuries="FutureInjuryB", # Current / Future injury
        recent_results="LLLLLLLLLL"
    )
    db_session.add_all([t_a, t_b])
    db_session.commit()
    db_session.refresh(t_a)
    db_session.refresh(t_b)

    # 2. Match 1: Played at Day 1, no AI Report exists.
    # We expect features reconstructed to use historical recent_results to compute form_factor.
    m1 = Match(
        match_code="M1",
        home_team_id=t_a.id,
        away_team_id=t_b.id,
        status=MatchStatus.FINISHED,
        actual_home_goals=1,
        actual_away_goals=0,
        actual_outcome="home",
        kickoff_at=datetime.datetime(2026, 6, 1, 12, 0, 0),
        odds_home=1.8, odds_draw=3.2, odds_away=4.0
    )
    
    # Match 2: Played at Day 2, has AI Report with JSON_SNAPSHOT.
    # We expect features reconstructed to retrieve values from JSON_SNAPSHOT.
    m2 = Match(
        match_code="M2",
        home_team_id=t_a.id,
        away_team_id=t_b.id,
        status=MatchStatus.FINISHED,
        actual_home_goals=2,
        actual_away_goals=1,
        actual_outcome="home",
        kickoff_at=datetime.datetime(2026, 6, 2, 12, 0, 0),
        odds_home=1.8, odds_draw=3.2, odds_away=4.0
    )
    
    db_session.add_all([m1, m2])
    db_session.commit()
    db_session.refresh(m1)
    db_session.refresh(m2)

    # Create predictions so WeightLearner will fetch them
    p1 = Prediction(match_id=m1.id, play_type=PlayType.SPF, probabilities={"home": 0.5, "draw": 0.3, "away": 0.2})
    p2 = Prediction(match_id=m2.id, play_type=PlayType.SPF, probabilities={"home": 0.5, "draw": 0.3, "away": 0.2})
    db_session.add_all([p1, p2])
    
    # Create MatchAIReport with JSON_SNAPSHOT for Match 2
    report_md = """# AI Report
    Some markdown contents here.
    <!-- JSON_SNAPSHOT: {"home_factor": 1.45, "away_factor": 0.55, "home_morale": 0.45, "away_morale": -0.45, "home_injuries": "Messi(伤)", "away_injuries": "None"} -->
    """
    report = MatchAIReport(match_id=m2.id, content=report_md)
    db_session.add(report)
    db_session.commit()

    # 3. Execute weight learner training data fetching (which calls _reconstruct_context)
    # Inside fetch_training_data, the chronological state updates will build history:
    # Match 1 (Day 1): Home Team A starts with historical results empty.
    # For Match 1, we pass recent_results empty to _reconstruct_context, which computes fallback.
    # Match 2 (Day 2): Team A has result from Match 1 ("W" since Match 1 actual_outcome was "home").
    # For Match 2, since AI report exists, it extracts snapshot.
    learner = WeightLearner(db_session)
    training_data = learner.fetch_training_data()
    
    # We should have retrieved 2 matches
    assert len(training_data) == 2
    
    ctx1, out1 = training_data[0]
    ctx2, out2 = training_data[1]
    
    # Verify Match 1 (Day 1) Reconstruction:
    # No AI Report -> Fallback to recent_results (which is empty initially).
    # _compute_form_factor("") should return 1.0.
    # Current Team.form_factor (1.50) must NOT leak here.
    assert ctx1.home_team.form_factor == 1.0
    assert ctx1.away_team.form_factor == 1.0
    assert ctx1.home_team.key_injuries == ""
    assert ctx1.away_team.key_injuries == ""
    
    # Verify Match 2 (Day 2) Reconstruction:
    # AI Report exists -> Retrieve from JSON_SNAPSHOT: home_factor=1.45, away_factor=0.55, home_injuries="Messi(伤)", away_injuries="None"
    # Current Team.form_factor (1.50 and 0.60) must NOT leak here.
    assert ctx2.home_team.form_factor == 1.45
    assert ctx2.away_team.form_factor == 0.55
    assert ctx2.home_team.key_injuries == "Messi(伤)"
    assert ctx2.away_team.key_injuries == "None"
