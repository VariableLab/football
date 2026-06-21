import pytest
import os
from database.models import Match, MatchStatus
from footy.content.engine import ContentSynthesizer

@pytest.fixture
def db_session():
    """Provides a clean in-memory database session for testing."""
    from database.models import SessionLocal, Base, engine
    session = SessionLocal()
    Base.metadata.create_all(bind=engine)
    yield session
    Base.metadata.drop_all(bind=engine)
    session.close()

def test_content_engine_dummy_generation(db_session):
    """
    Test the ContentSynthesizer pipeline with use_dummy=True
    to ensure v4.0 probabilities and StatsBomb dummy data are merged correctly into the JSON schema.
    """
    # Create a mock match if not exists
    match = db_session.query(Match).filter(Match.status == MatchStatus.SCHEDULED).first()
    if not match:
        from database.models import Team
        t_h = Team(name="Test Home", code="TH")
        t_a = Team(name="Test Away", code="TA")
        db_session.add_all([t_h, t_a])
        db_session.commit()
        match = Match(
            match_code="TEST-001",
            home_team=t_h,
            away_team=t_a,
            status=MatchStatus.SCHEDULED,
            competition="Test Cup"
        )
        db_session.add(match)
        db_session.commit()
        
    # Force dummy mode to avoid API call
    os.environ.pop("GEMINI_API_KEY", None)
    synth = ContentSynthesizer(gemini_api_key=None)
    synth.db = db_session  # Inject test session
    synth.pred_engine.db = db_session # Inject test session
    synth.use_dummy = True
    
    result = synth.generate_preview(match.match_code)
    
    assert "match_id" in result
    assert result["match_id"] == match.match_code
    assert "ai_predictions" in result
    assert "content_cards" in result
    
    ai_preds = result["ai_predictions"]
    assert "home_win_prob" in ai_preds
    assert "recommended_score" in ai_preds
    assert len(ai_preds["recommended_score"]) > 0
    
    cards = result["content_cards"]
    assert "preview" in cards
    assert "title" in cards["preview"]
    assert "xg_analysis" in cards
    assert "home_xg" in cards["xg_analysis"]
