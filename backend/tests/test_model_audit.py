"""Tests for model_audit — AuditReport, self-heal lock/cooldown."""
import json
import os
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock

import pytest

@pytest.fixture(autouse=True)
def clean_audit_dir():
    from monitor.model_audit import AUDIT_DIR
    if os.path.exists(AUDIT_DIR):
        for f in os.listdir(AUDIT_DIR):
            if f.startswith("audit_") and f.endswith(".json"):
                os.remove(os.path.join(AUDIT_DIR, f))
    yield


from model_audit import AuditEntry, AuditReport, ModelAuditor, run_self_heal_cycle


class TestAuditEntry:
    """AuditEntry must store data correctly."""

    def test_create_entry(self):
        e = AuditEntry(
            match_id=1, match_code="BRA-ARG",
            predicted="home", actual="home",
            confidence=0.75, correct=True,
            prob_home=0.6, prob_draw=0.25, prob_away=0.15,
            is_high_prob=True,
        )
        assert e.correct is True
        assert e.predicted == "home"

    def test_incorrect_prediction(self):
        e = AuditEntry(
            match_id=2, match_code="FRA-ENG",
            predicted="home", actual="away",
            confidence=0.55, correct=False,
            prob_home=0.45, prob_draw=0.30, prob_away=0.25,
            is_high_prob=False,
        )
        assert e.correct is False


class TestAuditReport:
    """AuditReport must calculate metrics via _build_report."""

    def test_empty_report(self):
        report = AuditReport(date="2026-05-16")
        assert report.total == 0
        assert report.correct == 0
        assert report.direction_accuracy == 0.0

    def test_report_with_entries(self):
        entries = [
            AuditEntry(1, "A-B", "home", "home", 0.8, True, 0.6, 0.25, 0.15, True),
            AuditEntry(2, "C-D", "home", "draw", 0.7, False, 0.55, 0.30, 0.15, True),
            AuditEntry(3, "E-F", "away", "away", 0.6, True, 0.2, 0.3, 0.5, False),
        ]
        brier = 0.0
        for e in entries:
            for sel in ["home", "draw", "away"]:
                p = getattr(e, f"prob_{sel}")
                o = 1.0 if sel == e.actual else 0.0
                brier += (p - o) ** 2
        auditor = ModelAuditor()
        report = auditor._build_report(entries, brier)
        assert report.total == 3
        assert report.correct == 2
        assert report.direction_accuracy == 2 / 3

    def test_brier_score_perfect(self):
        entries = [
            AuditEntry(1, "A-B", "home", "home", 1.0, True, 1.0, 0.0, 0.0, True),
            AuditEntry(2, "C-D", "away", "away", 1.0, True, 0.0, 0.0, 1.0, True),
        ]
        brier = 0.0
        for e in entries:
            for sel in ["home", "draw", "away"]:
                p = getattr(e, f"prob_{sel}")
                o = 1.0 if sel == e.actual else 0.0
                brier += (p - o) ** 2
        auditor = ModelAuditor()
        with patch.object(auditor, '_check_drift'):
            report = auditor._build_report(entries, brier, rps_sum=0.0)
        assert report.brier_score == 0.0

    def test_rps_score_calculation(self):
        # 预测为 home [0.6, 0.3, 0.1]，实际为 home。
        # cum_p1 = 0.6, cum_o1 = 1.0 -> (0.6 - 1.0)^2 = 0.16
        # cum_p2 = 0.9, cum_o2 = 1.0 -> (0.9 - 1.0)^2 = 0.01
        # RPS = 0.5 * (0.16 + 0.01) = 0.085
        e = AuditEntry(1, "A", "home", "home", 0.6, True, 0.6, 0.3, 0.1, True)
        auditor = ModelAuditor()
        report = auditor._build_report([e], brier_sum=0.17, rps_sum=0.085)
        assert abs(report.rps_score - 0.085) < 1e-6


class TestSelfHealCycle:
    """run_self_heal_cycle() must handle lock/cooldown correctly."""

    def test_self_heal_lock_prevents_concurrent(self):
        mock_lock = MagicMock()
        mock_lock.acquire.return_value = False
        with patch("model_audit._self_heal_lock", mock_lock):
            result = run_self_heal_cycle("test")
            assert result["status"] == "skipped"
            assert result["reason"] == "another_self_heal_running"

    def test_self_heal_cooldown(self, tmp_path):
        state = {
            "status": "idle",
            "last_run": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),
            "last_result": {"status": "success"},
        }
        state_file = os.path.join(str(tmp_path), "self_heal_state.json")
        with open(state_file, "w") as f:
            json.dump(state, f)
        with patch("model_audit.SELF_HEAL_STATE_PATH", state_file):
            result = run_self_heal_cycle("test")
            assert result["status"] == "skipped"
            assert result["reason"] == "cooldown"

    def test_self_heal_full_cycle(self, tmp_path):
        state_file = os.path.join(str(tmp_path), "self_heal_state.json")
        with open(state_file, "w") as f:
            json.dump({"status": "idle", "last_run": None, "last_result": None}, f)
        with patch("model_audit.SELF_HEAL_STATE_PATH", state_file), \
             patch("model_audit._step_weight_learn", return_value={"accuracy": 0.55}), \
             patch("model_audit._step_regenerate", return_value=15), \
             patch("model_audit._step_validate", return_value={"status": "passed", "improvement": 0.003}):
            result = run_self_heal_cycle("scheduled")
            assert result["status"] == "success"
            assert result["reason"] == "scheduled"
            assert result["weight_learn_results"]["accuracy"] == 0.55
            assert result["regenerate_count"] == 15

    def test_self_heal_weight_learn_failure(self, tmp_path):
        state_file = os.path.join(str(tmp_path), "self_heal_state.json")
        with open(state_file, "w") as f:
            json.dump({"status": "idle", "last_run": None, "last_result": None}, f)
        with patch("model_audit.SELF_HEAL_STATE_PATH", state_file), \
             patch("model_audit._step_weight_learn", return_value=None):
            result = run_self_heal_cycle("test")
            assert result["status"] == "failed"


class TestModelAuditor:
    """ModelAuditor must save and list reports."""

    def test_get_latest_reports_no_files(self, tmp_path):
        auditor = ModelAuditor()
        reports = auditor.get_latest_reports(5)
        assert reports == []

    def test_get_latest_reports_with_files(self, tmp_path):
        audit_dir = os.path.join(str(tmp_path), "audit")
        os.makedirs(audit_dir, exist_ok=True)
        for day in range(3):
            path = os.path.join(audit_dir, f"audit_2026-05-{16 - day}.json")
            with open(path, "w") as f:
                json.dump({"date": f"2026-05-{16 - day}", "total": 10}, f)
        with patch("model_audit.AUDIT_DIR", audit_dir):
            auditor = ModelAuditor()
            reports = auditor.get_latest_reports(2)
            assert len(reports) == 2
            assert reports[0]["date"] == "2026-05-16"
            assert reports[1]["date"] == "2026-05-15"
