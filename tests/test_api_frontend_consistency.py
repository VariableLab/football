"""验证前端实际访问的 ~50 个字段与 API 响应一致。"""

import os, sys
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
os.environ.setdefault("SECRET_KEY", "test-secret-key-not-for-production")

from schemas import (
    MatchOut, PredictionOut, StrategyPickOut,
    FeedbackOut,
    JingcaiIssueOut, JingcaiIssueMatchOut,
    JingcaiReportIssue, JingcaiReportMatch, BestPickOut,
)

BASE = os.environ.get("TEST_BASE", "http://localhost:8000")


def test_prediction_out_has_all_frontend_fields():
    fields = {*PredictionOut.model_fields.keys()}
    for f in ["id", "match_id", "play_type", "probabilities", "model_version", "input_checksum", "locked_at"]:
        assert f in fields, f"PredictionOut missing field: {f}"


def test_feedback_out_has_all_frontend_fields():
    fields = {*FeedbackOut.model_fields.keys()}
    for f in ["id", "category", "author", "content", "created_at", "likes"]:
        assert f in fields, f"FeedbackOut missing field: {f}"


def test_strategy_pick_out_has_all_frontend_fields():
    fields = {*StrategyPickOut.model_fields.keys()}
    for f in ["strategy_name", "risk_level", "ev", "play_label", "selection_label",
              "odds", "probability", "rationale", "stake_pct"]:
        assert f in fields, f"StrategyPickOut missing field: {f}"


def test_match_out_has_all_frontend_fields():
    fields = {*MatchOut.model_fields.keys()}
    for f in ["id", "home_team", "away_team", "kickoff_at", "match_type",
              "status", "group", "stage", "competition", "odds_home",
              "odds_draw", "odds_away", "odds_source", "odds_degraded",
              "updated_at", "predictions", "actual_home_goals",
              "actual_away_goals", "actual_outcome"]:
        assert f in fields, f"MatchOut missing field: {f}"


def test_jingcai_issue_match_out_has_odds_fields():
    fields = {*JingcaiIssueMatchOut.model_fields.keys()}
    for f in ["sequence", "handicap", "rq_odds", "score_odds", "goals_odds", "half_odds", "match"]:
        assert f in fields, f"JingcaiIssueMatchOut missing field: {f}"


def test_jingcai_issue_out_has_all_frontend_fields():
    fields = {*JingcaiIssueOut.model_fields.keys()}
    for f in ["id", "issue_id", "status", "issue_type", "matches"]:
        assert f in fields, f"JingcaiIssueOut missing field: {f}"


def test_best_pick_out_has_all_fields():
    fields = {*BestPickOut.model_fields.keys()}
    for f in ["play_type", "selection", "probability"]:
        assert f in fields, f"BestPickOut missing field: {f}"


def test_jingcai_report_issue_has_all_fields():
    fields = {*JingcaiReportIssue.model_fields.keys()}
    for f in ["issue_id", "issue_type", "status", "total_matches", "spf_hits", "accuracy", "r9_hits", "analysis", "matches"]:
        assert f in fields, f"JingcaiReportIssue missing field: {f}"


def test_jingcai_report_match_has_all_fields():
    fields = {*JingcaiReportMatch.model_fields.keys()}
    for f in ["sequence", "home", "away", "handicap", "best_pick", "actual_outcome", "correct"]:
        assert f in fields, f"JingcaiReportMatch missing field: {f}"


# ─── Live server tests ───

@pytest.mark.live
def test_live_strategy_response_structure():
    import requests
    resp = requests.get(f"{BASE}/api/matches?limit=1", timeout=10)
    matches = (resp.json() or {}).get("items") or resp.json() or []
    if not matches:
        pytest.skip("No matches found")
    match_id = matches[0]["id"]
    strat_resp = requests.get(f"{BASE}/api/matches/{match_id}/strategy", timeout=10)
    assert strat_resp.ok, f"Strategy endpoint returned {strat_resp.status_code}"
    data = strat_resp.json()
    assert "predictions" in data
    assert "strategies" in data
    for pred in data.get("predictions", []):
        for f in ["play_type", "probabilities", "model_version", "locked_at", "input_checksum"]:
            assert f in pred, f"Strategy prediction missing field: {f}"
    for s in data.get("strategies", []):
        for f in ["strategy_name", "risk_level", "ev", "play_label", "selection_label",
                  "odds", "probability", "rationale", "stake_pct"]:
            assert f in s, f"Strategy missing field: {f}"


@pytest.mark.live
def test_live_match_list_structure():
    import requests
    resp = requests.get(f"{BASE}/api/matches?limit=5", timeout=10)
    assert resp.ok
    data = resp.json()
    items = data.get("items") or data or []
    if not items:
        pytest.skip("No matches")
    m = items[0]
    for f in ["id", "home_team", "away_team", "kickoff_at", "match_type",
              "status", "odds_home", "odds_draw", "odds_away", "odds_source",
              "odds_degraded", "updated_at", "predictions",
              "actual_home_goals", "actual_away_goals", "actual_outcome"]:
        assert f in m, f"Match missing field: {f}"
    for side in ["home_team", "away_team"]:
        team = m.get(side)
        if team:
            for f in ["name", "flag"]:
                assert f in team, f"Team.{side} missing field: {f}"


@pytest.mark.live
def test_live_match_predictions_have_input_checksum():
    import requests
    resp = requests.get(f"{BASE}/api/matches?limit=5", timeout=10)
    data = resp.json()
    items = data.get("items") or data or []
    for m in items:
        for pred in m.get("predictions", []):
            for f in ["play_type", "probabilities", "model_version", "locked_at", "input_checksum"]:
                assert f in pred, f"Match prediction missing field: {f}"


@pytest.mark.live
def test_live_feedback_structure():
    import requests
    resp = requests.get(f"{BASE}/api/feedback", timeout=10)
    assert resp.ok
    data = resp.json()
    items = data.get("items") or data or []
    if not items:
        pytest.skip("No feedback")
    item = items[0]
    for f in ["id", "category", "author", "content", "created_at", "likes"]:
        assert f in item, f"Feedback missing field: {f}"


@pytest.mark.live
def test_live_jingcai_issues_structure():
    import requests
    resp = requests.get("http://localhost:8000/api/jingcai/issues?limit=3", timeout=10)
    assert resp.ok
    data = resp.json()
    assert "items" in data
    assert "total" in data
    if not data["items"]:
        pytest.skip("No jingcai issues")
    issue = data["items"][0]
    for f in ["id", "issue_id", "status", "issue_type", "matches"]:
        assert f in issue, f"Issue missing field: {f}"
    if issue.get("matches"):
        im = issue["matches"][0]
        for f in ["sequence", "handicap", "rq_odds", "score_odds", "goals_odds", "half_odds", "match"]:
            assert f in im, f"Issue match missing field: {f}"
        match = im.get("match", {})
        for f in ["id", "home_team", "away_team", "kickoff_at", "status", "odds_home", "odds_draw", "odds_away"]:
            assert f in match, f"Issue match.match missing field: {f}"
        if match.get("predictions"):
            pred = match["predictions"][0]
            for f in ["play_type", "probabilities", "model_version", "input_checksum", "locked_at"]:
                assert f in pred, f"Issue match prediction missing field: {f}"


@pytest.mark.live
def test_live_jingcai_report_structure():
    import requests
    resp = requests.get("http://localhost:8000/api/jingcai/report", timeout=10)
    assert resp.ok
    data = resp.json()
    assert "reports" in data
    if not data["reports"]:
        pytest.skip("No reports")
    report = data["reports"][0]
    for f in ["issue_id", "issue_type", "status", "total_matches", "spf_hits", "accuracy", "r9_hits", "analysis", "matches"]:
        assert f in report, f"Report missing field: {f}"
    if report.get("matches"):
        mr = report["matches"][0]
        for f in ["sequence", "home", "away", "handicap", "best_pick", "actual_outcome", "correct"]:
            assert f in mr, f"Report match missing field: {f}"
        if mr.get("best_pick"):
            for f in ["play_type", "selection", "probability"]:
                assert f in mr["best_pick"], f"Report best_pick missing field: {f}"
