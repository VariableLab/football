"""Tests for HealthDaemon — check logic and report generation."""
import json
import os
import sqlite3
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock

import pytest

from health_daemon import (
    HealthDaemon, HealthReport, CheckResult,
)


class TestCheckResult:
    """CheckResult and HealthReport must serialize correctly."""

    def test_check_result_defaults(self):
        r = CheckResult(name="test")
        assert r.status == "ok"
        assert r.message == ""
        assert r.repaired is False

    def test_health_report_to_dict(self):
        report = HealthReport(
            timestamp="2026-05-16T12:00:00",
            overall="degraded",
            checks=[
                CheckResult(name="db", status="ok", message="db OK"),
                CheckResult(name="odds", status="warn", message="odds stale",
                            repaired=True, repair_action="re-fetched"),
            ],
        )
        d = report.to_dict()
        assert d["overall"] == "degraded"
        assert len(d["checks"]) == 2
        assert d["checks"][1]["repaired"] is True
        assert d["checks"][1]["repair_action"] == "re-fetched"


class TestHealthDaemon:
    """HealthDaemon check methods must handle various states."""

    def test_db_integrity_missing_file(self, tmp_path):
        daemon = HealthDaemon()
        with patch("health_daemon.DB_PATH", os.path.join(str(tmp_path), "nonexistent.sqlite")):
            daemon._check_db_integrity()
        check = [c for c in daemon._report.checks if c.name == "db_integrity"]
        assert len(check) == 1
        assert check[0].status == "fail"

    def test_db_integrity_ok(self, tmp_path):
        db_path = os.path.join(str(tmp_path), "test.sqlite")
        conn = sqlite3.connect(db_path)
        conn.close()
        daemon = HealthDaemon()
        with patch("health_daemon.DB_PATH", db_path):
            daemon._check_db_integrity()
        check = [c for c in daemon._report.checks if c.name == "db_integrity"]
        assert len(check) == 1
        assert check[0].status == "ok"

    def test_db_integrity_corrupt(self, tmp_path):
        db_path = os.path.join(str(tmp_path), "corrupt.sqlite")
        with open(db_path, "wb") as f:
            f.write(b"not a valid sqlite database")
        daemon = HealthDaemon()
        with patch("health_daemon.DB_PATH", db_path):
            daemon._check_db_integrity()
        check = [c for c in daemon._report.checks if c.name == "db_integrity"]
        assert check[0].status != "ok"

    def test_overall_critical_when_any_fail(self):
        daemon = HealthDaemon()
        daemon._report.checks = [
            CheckResult(name="db", status="ok"),
            CheckResult(name="odds", status="fail"),
        ]
        daemon._determine_overall()
        assert daemon._report.overall == "critical"

    def test_overall_degraded_when_any_warn(self):
        daemon = HealthDaemon()
        daemon._report.checks = [
            CheckResult(name="db", status="ok"),
            CheckResult(name="odds", status="warn"),
        ]
        daemon._determine_overall()
        assert daemon._report.overall == "degraded"

    def test_overall_ok_when_all_ok(self):
        daemon = HealthDaemon()
        daemon._report.checks = [
            CheckResult(name="db", status="ok"),
            CheckResult(name="odds", status="ok"),
        ]
        daemon._determine_overall()
        assert daemon._report.overall == "ok"

    def test_persist_report(self, tmp_path):
        health_file = os.path.join(str(tmp_path), "health_status.json")
        daemon = HealthDaemon()
        daemon._report = HealthReport(
            timestamp="2026-05-16T12:00:00",
            overall="ok",
            checks=[CheckResult(name="db", status="ok", message="all good")],
        )
        with patch("health_daemon.HEALTH_FILE", health_file):
            daemon._persist_report()
        assert os.path.exists(health_file)
        with open(health_file) as f:
            data = json.load(f)
        assert data["overall"] == "ok"

    def test_consecutive_failures_detected(self):
        daemon = HealthDaemon()
        alerts = [
            {"source": "sync_odds", "message": "sync_odds failed", "ts": datetime.now(timezone.utc).timestamp()},
            {"source": "sync_odds", "message": "sync_odds error", "ts": datetime.now(timezone.utc).timestamp()},
            {"source": "sync_odds", "message": "sync_odds 失败", "ts": datetime.now(timezone.utc).timestamp()},
        ]
        with patch("health_daemon.get_active_alerts", return_value=alerts):
            daemon._check_consecutive_failures()
        check = [c for c in daemon._report.checks if c.name == "consecutive_failures"]
        assert check[0].status == "warn"

    def test_consecutive_failures_ok(self):
        daemon = HealthDaemon()
        with patch("health_daemon.get_active_alerts", return_value=[]):
            daemon._check_consecutive_failures()
        check = [c for c in daemon._report.checks if c.name == "consecutive_failures"]
        assert check[0].status == "ok"
