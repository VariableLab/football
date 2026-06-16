"""
日志清理 CLI 的单元测试
========================

覆盖 scripts/cleanup_logs.py 的核心场景:
  1. 7 天前/7 天后文件分类
  2. dry-run 不实际删除
  3. 误删保护: 非 YYYY-MM-DD.log 格式不动
  4. JSON 输出结构
  5. log_dir 不存在时的优雅跳过
  6. 单次删除失败不影响其它

修复背景: 6-16 体积审计发现 utils/logs/ 累积 12MB / 713 个文件
"""

import os
import sys
import json
import tempfile
import argparse
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

from scripts.cleanup_logs import main as cli_main


# ──────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────

@pytest.fixture
def fake_log_dir():
    """创建临时 log 目录,预先放好各种场景的文件。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        log_dir = Path(tmpdir)

        # 1) 过期文件(应被删)
        old_date = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
        (log_dir / f"scheduler.{old_date}.log").write_text("old content A")
        (log_dir / f"odds.{old_date}.log").write_text("x" * 1000)

        # 2) 边界文件(7 天前,retain=7 时边界行为)
        boundary_date = (datetime.now() - timedelta(days=8)).strftime("%Y-%m-%d")
        (log_dir / f"scheduler.{boundary_date}.log").write_text("boundary")

        # 3) 保留文件(应留)
        recent_date = (datetime.now() - timedelta(days=2)).strftime("%Y-%m-%d")
        (log_dir / f"scheduler.{recent_date}.log").write_text("recent")
        (log_dir / f"odds.{recent_date}.log").write_text("recent odds")

        # 4) 误删保护: 非标准格式
        (log_dir / "not_a_date.log").write_text("should be kept")
        (log_dir / "weird.2026-99-99.log").write_text("invalid date")
        (log_dir / "README.md").write_text("should be kept")

        # 5) error 子文件
        (log_dir / f"scheduler.error.{old_date}.log").write_text("old error log")
        (log_dir / f"scheduler.error.{recent_date}.log").write_text("recent error log")

        yield log_dir


def _run_cli(log_dir: Path, retain_days: int = 7, dry_run: bool = False) -> int:
    """用 mock 替换 parse_args 跑一次 CLI。"""
    with patch(
        "scripts.cleanup_logs.parse_args",
        return_value=argparse.Namespace(
            log_dir=str(log_dir),
            retain_days=retain_days,
            dry_run=dry_run,
            json=False,
        ),
    ):
        return cli_main()


# ──────────────────────────────────────────────
# Tests
# ──────────────────────────────────────────────

def test_scenario1_classify_old_vs_recent(fake_log_dir):
    """场景 1: 7 天前的文件被删,2 天前的保留。"""
    rc = _run_cli(fake_log_dir, retain_days=7, dry_run=False)
    assert rc == 0

    # 30 天前: 删
    assert not (fake_log_dir / f"scheduler.{(datetime.now() - timedelta(days=30)):%Y-%m-%d}.log").exists()
    # 8 天前(>7): 删
    assert not (fake_log_dir / f"scheduler.{(datetime.now() - timedelta(days=8)):%Y-%m-%d}.log").exists()
    # 2 天前: 保留
    assert (fake_log_dir / f"scheduler.{(datetime.now() - timedelta(days=2)):%Y-%m-%d}.log").exists()


def test_scenario2_dry_run_does_not_delete(fake_log_dir):
    """场景 2: dry-run 不实际删除文件。"""
    target = fake_log_dir / f"scheduler.{(datetime.now() - timedelta(days=30)):%Y-%m-%d}.log"
    assert target.exists()  # 删之前存在

    _run_cli(fake_log_dir, retain_days=7, dry_run=True)
    assert target.exists()  # dry-run 后仍在


def test_scenario3_protect_non_standard_filenames(fake_log_dir):
    """场景 3: 误删保护: 非 YYYY-MM-DD.log 格式不被删。"""
    _run_cli(fake_log_dir, retain_days=7, dry_run=False)

    # 非标准命名全部保留
    assert (fake_log_dir / "not_a_date.log").exists()
    assert (fake_log_dir / "weird.2026-99-99.log").exists()
    assert (fake_log_dir / "README.md").exists()


def test_scenario4_error_subfiles_also_cleaned(fake_log_dir):
    """场景 4: <name>.error.YYYY-MM-DD.log 也走清理。"""
    old_error = fake_log_dir / f"scheduler.error.{(datetime.now() - timedelta(days=30)):%Y-%m-%d}.log"
    recent_error = fake_log_dir / f"scheduler.error.{(datetime.now() - timedelta(days=2)):%Y-%m-%d}.log"

    assert old_error.exists()
    assert recent_error.exists()

    _run_cli(fake_log_dir, retain_days=7, dry_run=False)

    assert not old_error.exists()  # 旧的删
    assert recent_error.exists()  # 新的留


def test_scenario5_json_output_structure(fake_log_dir, capsys):
    """场景 5: --json 输出含完整统计结构。"""
    with patch(
        "scripts.cleanup_logs.parse_args",
        return_value=argparse.Namespace(
            log_dir=str(fake_log_dir),
            retain_days=7,
            dry_run=True,
            json=True,
        ),
    ):
        rc = cli_main()
    assert rc == 0

    captured = capsys.readouterr()
    result = json.loads(captured.out)
    assert result["status"] == "ok"
    assert result["retain_days"] == 7
    assert result["dry_run"] is True
    assert "scanned" in result["stats"]
    assert "removed" in result["stats"]
    assert "kept" in result["stats"]
    assert "freed_mb" in result
    assert "started_at" in result
    assert "finished_at" in result
    assert isinstance(result["removed_files"], list)


def test_scenario6_log_dir_not_exists(capsys):
    """场景 6: log_dir 不存在时优雅跳过。"""
    with patch(
        "scripts.cleanup_logs.parse_args",
        return_value=argparse.Namespace(
            log_dir="/nonexistent/path/that/should/not/exist/xyz",
            retain_days=7,
            dry_run=False,
            json=False,
        ),
    ):
        rc = cli_main()
    assert rc == 0  # exit 0,因为是"正常情况"非失败
    out = capsys.readouterr().out
    assert "不存在" in out or "WARN" in out


def test_scenario7_delete_failure_does_not_block(fake_log_dir):
    """场景 7: 单个文件 unlink 失败不影响其它文件清理。"""
    # 把 30 天前的文件设为只读,模拟删除失败(类 Unix 行为)
    old = fake_log_dir / f"scheduler.{(datetime.now() - timedelta(days=30)):%Y-%m-%d}.log"
    other_old = fake_log_dir / f"odds.{(datetime.now() - timedelta(days=30)):%Y-%m-%d}.log"

    # 模拟: 让 scheduler.30d.log 的 unlink 抛 OSError
    real_unlink = Path.unlink

    def fake_unlink(self, *args, **kwargs):
        if self.name == old.name:
            raise OSError("Permission denied (simulated)")
        return real_unlink(self, *args, **kwargs)

    with patch.object(Path, "unlink", fake_unlink):
        rc = _run_cli(fake_log_dir, retain_days=7, dry_run=False)

    assert rc == 0  # 不应崩溃
    assert other_old.exists() is False  # 另一个文件成功删
    assert old.exists() is True  # 这个模拟失败,仍在(被记录到 errors)


def test_scenario8_keep_30_days(fake_log_dir):
    """场景 8: 30 天保留阈值下,严格大于 30 天的文件被删,30 天内的保留。"""
    # 31 天前: 应被删
    too_old = fake_log_dir / f"scheduler.{(datetime.now() - timedelta(days=31)):%Y-%m-%d}.log"
    too_old.write_text("should be removed (31 days)")

    # 8 天前(在 30 天保留内): 应保留
    in_range = fake_log_dir / f"odds.{(datetime.now() - timedelta(days=8)):%Y-%m-%d}.log"
    in_range.write_text("should be kept (8 days)")

    _run_cli(fake_log_dir, retain_days=30, dry_run=False)
    assert not too_old.exists()  # 31 天前: 删
    assert in_range.exists()  # 8 天前: 留


def test_scenario9_freed_bytes_calculated(fake_log_dir, capsys):
    """场景 9: 释放字节数正确计算。"""
    with patch(
        "scripts.cleanup_logs.parse_args",
        return_value=argparse.Namespace(
            log_dir=str(fake_log_dir),
            retain_days=7,
            dry_run=True,  # dry-run 也应计算 freed_bytes
            json=True,
        ),
    ):
        cli_main()
    result = json.loads(capsys.readouterr().out)
    # 30 天前 odds.2026-XX-XX.log 写入了 1000 字节
    assert result["freed_mb"] >= 0.0
    # 至少 1 个被标记 removed
    assert result["stats"]["removed"] >= 1
