"""Tests for Kelly formula integration and value bet filtering in tiered strategy."""
import os
import sys
import pytest

# Add backend and subdirectories to sys.path
_tests_dir = os.path.dirname(os.path.abspath(__file__))
_backend_root = os.path.dirname(_tests_dir)
for d in ["", "api", "core", "features", "ingestion", "database", "strategy", "monitor", "utils", "scripts"]:
    path = os.path.join(_backend_root, d)
    if path not in sys.path:
        sys.path.insert(0, path)

from tiered_strategy import _recommend_spf, analyze_match, TIER_HIGH
from strategy_config import load_params
from position_sizer import PositionSizer, RiskTier

def test_spf_kelly_position_and_value_filtering():
    """
    Verify that _recommend_spf correctly identifies value bets, 
    applies the Kelly formula to compute stake_pct,
    and filters out negative EV bets.
    """
    params = load_params()
    sizer = PositionSizer(RiskTier.BALANCED)
    
    # ─── Case 1: Positive EV Bet (Value Bet) ───
    # Prob = 0.60, Odds = 1.90. 
    # EV = 0.60 * 1.90 - 1 = +14% (Positive!)
    spf_probs = {"home": 0.60, "draw": 0.20, "away": 0.20}
    nn_values = {"home": 0.55, "draw": 0.25, "away": 0.20}
    odds = {"home": 1.90, "draw": 3.40, "away": 4.00}
    
    rec = _recommend_spf(
        spf_probs=spf_probs,
        nn_values=nn_values,
        odds=odds,
        tier=TIER_HIGH,
        params=params,
        sizer=sizer,
        bankroll=1000.0,
        peak=1000.0
    )
    
    # It must be recommended
    assert rec.is_recommended is True
    assert rec.ev > 0
    assert rec.edge > 0
    assert rec.kelly_raw > 0
    assert rec.stake_pct > 0
    assert rec.stake_amount > 0
    assert "凯利仓位" in rec.reason

    # ─── Case 2: Negative EV Bet (Filtered Out) ───
    # Prob = 0.45 (passes minimum confidence 0.35), Odds = 1.60. (above skip threshold 1.50)
    # EV = 0.45 * 1.60 - 1 = -28% (Negative EV!)
    spf_probs_neg = {"home": 0.45, "draw": 0.25, "away": 0.30}
    nn_values_neg = {"home": 0.45, "draw": 0.25, "away": 0.30}
    odds_neg = {"home": 1.60, "draw": 3.00, "away": 3.00}
    
    rec_neg = _recommend_spf(
        spf_probs=spf_probs_neg,
        nn_values=nn_values_neg,
        odds=odds_neg,
        tier=TIER_HIGH,
        params=params,
        sizer=sizer,
        bankroll=1000.0,
        peak=1000.0
    )
    
    # Negative EV must be filtered out
    assert rec_neg.is_recommended is False
    assert rec_neg.ev < 0
    assert rec_neg.stake_pct == 0.0
    assert rec_neg.stake_amount == 0.0
    assert "期望价值EV<=0" in rec_neg.reason

def test_analyze_match_kelly_integration():
    """
    Verify that analyze_match integrates Kelly position and edge values
    into the final MatchTierResult.
    """
    spf_probs = {"home": 0.65, "draw": 0.20, "away": 0.15}
    nn_values = {"home": 0.60, "draw": 0.20, "away": 0.20}
    odds = {"home": 1.85, "draw": 3.50, "away": 4.50}
    
    result = analyze_match(
        match_id=42,
        spf_probs=spf_probs,
        nn_values=nn_values,
        odds=odds,
        is_jingcai=False,
        sub_model_results={},
        match_code="TEST-42",
        bankroll=2000.0,
        peak=2000.0,
        risk_tier="balanced"
    )
    
    assert result.match_id == 42
    assert result.edge > 0
    assert result.ev > 0
    assert result.kelly_raw > 0
    assert result.stake_pct > 0
    assert result.stake_amount > 0
    
    # stake_amount should scale with bankroll (2000.0), checking with tolerance for roundings
    assert result.stake_amount == pytest.approx(2000.0 * result.stake_pct, abs=0.2)
