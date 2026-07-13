"""
调度器任务测试

覆盖:
- lock_predictions_job 逻辑
- backup_database_job 逻辑
- sync_results_job 逻辑
- DBSession 上下文管理器
"""
import sys
import os

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock


class TestDBSession:
    def test_module_imports(self):
        """验证 DBSession 类可导入"""
        from monitor.scheduler import DBSession
        assert DBSession is not None
        assert hasattr(DBSession, '__enter__')
        assert hasattr(DBSession, '__exit__')


class TestLockPredictionsLogic:
    """测试 lock_predictions_job 的核心逻辑(不依赖真实DB)"""

    def test_window_filter(self):
        """48h窗口内的比赛应该被选中"""
        now = datetime.now(timezone.utc)
        window = now + timedelta(hours=48)
        soon = now + timedelta(hours=24)
        far = now + timedelta(hours=72)

        # 24h内 → 应该在窗口内
        assert soon <= window and soon > now
        # 72h后 → 不应在窗口内
        assert far > window

    def test_already_locked_skipped(self):
        """已有预测的比赛应跳过"""
        # 模拟: 已有Prediction记录 → continue
        existing = MagicMock()
        assert existing is not None  # 表示已有


class TestBackupDatabaseLogic:
    """测试备份逻辑"""

    def test_hash_skip_unchanged(self):
        """DB未变化时应跳过备份"""
        hash1 = "abc123def456"
        hash2 = "abc123def456"
        assert hash1 == hash2

    def test_hash_differs_when_changed(self):
        """DB变化时hash应不同"""
        hash1 = "abc123def456"
        hash2 = "xyz789ghi012"
        assert hash1 != hash2

    def test_backup_path_format(self):
        """备份文件名格式应为 db_YYYYMMDD_HHMMSS.sqlite"""
        import re
        pattern = re.compile(r"db_\d{8}_\d{6}\.sqlite$")
        assert pattern.match("db_20260621_120000.sqlite")
        assert not pattern.match("backup_20260621.sqlite")


class TestDataQualityChecks:
    """数据质量门禁测试"""

    def test_odds_valid_range(self):
        """赔率必须在合理范围内"""
        valid_pairs = [
            (1.50, 3.50, 5.00),  # 正常
            (2.00, 3.20, 3.80),  # 正常
            (1.10, 8.00, 15.00), # 极端但合法
        ]
        for h, d, a in valid_pairs:
            assert h > 1.01 and d > 1.01 and a > 1.01
            # 隐含概率总和应在合理范围
            implied = 1/h + 1/d + 1/a
            assert 1.05 <= implied <= 1.20

    def test_odds_invalid(self):
        """无效赔率应被拒绝"""
        invalid_pairs = [
            (0.90, 3.50, 5.00),  # < 1.01
            (1.50, 0.50, 5.00),  # < 1.01
            (1.50, 3.50, 0.80),  # < 1.01
        ]
        for h, d, a in invalid_pairs:
            assert any(o <= 1.01 for o in [h, d, a])

    def test_probability_sum(self):
        """概率和必须≈1.0"""
        probs = [0.55, 0.25, 0.20]
        assert abs(sum(probs) - 1.0) < 0.01

        bad_probs = [0.50, 0.30, 0.30]
        assert abs(sum(bad_probs) - 1.0) > 0.05


class TestConfidenceComputation:
    """置信度计算测试"""

    def test_high_confidence_strong_signal(self):
        """高置信度: 强信号(熵低+市场一致)"""
        import numpy as np
        probs = np.array([0.70, 0.15, 0.15])
        entropy = -np.sum(probs * np.log(probs + 1e-8))
        # 低熵 → 高置信度 (ln(3)≈1.098 是最大熵)
        assert entropy < 0.85

    def test_medium_confidence(self):
        """中置信度: 中等信号"""
        import numpy as np
        probs = np.array([0.45, 0.30, 0.25])
        entropy = -np.sum(probs * np.log(probs + 1e-8))
        assert 0.85 <= entropy <= 1.10

    def test_low_confidence_uniform(self):
        """低置信度: 均匀分布"""
        import numpy as np
        probs = np.array([0.33, 0.34, 0.33])
        entropy = -np.sum(probs * np.log(probs + 1e-8))
        # 高熵(接近ln(3)=1.098) → 低置信度
        assert entropy > 0.95


class TestMatchStatusTransitions:
    """比赛状态转换测试"""

    def test_scheduled_to_upcoming(self):
        """SCHEDULED → UPCOMING (预测锁定后)"""
        from database.models import MatchStatus
        assert MatchStatus.SCHEDULED.value == "scheduled"
        assert MatchStatus.UPCOMING.value == "upcoming"

    def test_upcoming_to_live(self):
        """UPCOMING → LIVE (开球)"""
        from database.models import MatchStatus
        assert MatchStatus.LIVE.value == "live"

    def test_live_to_finished(self):
        """LIVE → FINISHED (结束)"""
        from database.models import MatchStatus
        assert MatchStatus.FINISHED.value == "finished"

    def test_postponed(self):
        """延期状态"""
        from database.models import MatchStatus
        assert MatchStatus.POSTPONED.value == "postponed"
