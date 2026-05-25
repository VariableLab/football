"""
Health Daemon — 自检 + 自修引擎
每 10 分钟运行一次，检查各子系统健康状态，触发自动修复。
"""
import sqlite3
import os
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass, field
from typing import Optional

from utils.logger import get_logger
from alert_manager import fire_alert, get_active_alerts, check_consecutive_failures, odds_freshness_check

logger = get_logger("health_daemon")

_BASE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(_BASE, "database.sqlite")
BACKUP_DIR = os.path.join(_BASE, "backup")
HEALTH_FILE = os.path.join(_BASE, "data", "health_status.json")

# 阈值常量
ODDS_STALE_HOURS = 2
DRIFT_ACCURACY_FLOOR = 0.48  # 低于48%视为漂移（随机=33% for 3-way, 但SPF通常略高于50%）
MIN_PREDICTIONS_FOR_DRIFT = 20
DB_INTEGRITY_RETRIES = 2
MAX_CONSECUTIVE_JOB_FAILURES = 3


@dataclass
class CheckResult:
    name: str
    status: str = "ok"  # ok | warn | fail
    message: str = ""
    repaired: bool = False
    repair_action: str = ""


@dataclass
class HealthReport:
    timestamp: str = ""
    overall: str = "ok"  # ok | degraded | critical
    checks: list[CheckResult] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "overall": self.overall,
            "checks": [
                {"name": c.name, "status": c.status, "message": c.message,
                 "repaired": c.repaired, "repair_action": c.repair_action}
                for c in self.checks
            ],
        }


class HealthDaemon:
    """自检 + 自修引擎"""

    def __init__(self) -> None:
        self._report = HealthReport()
        self._drift_alert_cooldown: float = 0  # 上次发射模型漂移告警的时间戳

    # ────────────────────────────
    # 主入口
    # ────────────────────────────
    def run_all_checks(self) -> HealthReport:
        self._report = HealthReport(timestamp=datetime.now(timezone.utc).isoformat())

        self._check_db_integrity()
        self._check_odds_freshness()
        self._check_scheduler_jobs()
        self._check_data_completeness()
        self._check_zgzcw_sync()
        self._check_jingcai_issues()
        self._check_model_drift()
        self._check_consecutive_failures()
        self._check_backup_freshness()

        self._determine_overall()
        self._persist_report()
        return self._report

    # ────────────────────────────
    # Check 1: DB 完整性
    # ────────────────────────────
    def _check_db_integrity(self) -> None:
        result = CheckResult(name="db_integrity")

        if not os.path.exists(DB_PATH):
            result.status = "fail"
            result.message = f"数据库文件不存在: {DB_PATH}"
            self._report.checks.append(result)
            return

        try:
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.execute("PRAGMA integrity_check")
            status = cursor.fetchone()[0]
            conn.close()

            if status == "ok":
                result.message = "SQLite integrity OK"
            else:
                result.status = "fail"
                result.message = f"SQLite integrity FAIL: {status}"
                self._attempt_db_repair(result)
        except Exception as e:
            result.status = "fail"
            result.message = f"DB check error: {e}"
            self._attempt_db_repair(result)

        self._report.checks.append(result)

    def _attempt_db_repair(self, result: CheckResult) -> None:
        """尝试从最近的备份恢复数据库"""
        import glob
        backups = sorted(glob.glob(f"{BACKUP_DIR}/db_*.sqlite"), reverse=True)

        if not backups:
            fire_alert("health_daemon", "critical", "DB损坏且无可用备份")
            result.repair_action = "no_backup_available"
            return

        latest_backup = backups[0]
        try:
            import shutil
            # 先备份损坏的文件
            corrupt_path = f"{DB_PATH}.corrupt.{datetime.now().strftime('%Y%m%d%H%M%S')}"
            shutil.move(DB_PATH, corrupt_path)
            logger.warning(f"[health] Corrupt DB moved to {corrupt_path}")

            # 从备份恢复
            src = sqlite3.connect(latest_backup)
            dst = sqlite3.connect(DB_PATH)
            src.backup(dst)
            dst.close()
            src.close()

            # 验证恢复后的数据库
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.execute("PRAGMA integrity_check")
            status = cursor.fetchone()[0]
            conn.close()

            if status == "ok":
                result.repaired = True
                result.repair_action = f"restored from {latest_backup}"
                fire_alert("health_daemon", "critical",
                           f"DB损坏已自动恢复，来源: {latest_backup}")
            else:
                result.repair_action = f"restore failed, backup also corrupt: {latest_backup}"
                fire_alert("health_daemon", "critical", "DB恢复失败，备份也损坏")
        except Exception as e:
            result.repair_action = f"repair error: {e}"
            fire_alert("health_daemon", "critical", f"DB修复异常: {e}")

    # ────────────────────────────
    # Check 2: 赔率新鲜度
    # ────────────────────────────
    def _check_odds_freshness(self) -> None:
        result = CheckResult(name="odds_freshness")

        from database.models import SessionLocal
        session = SessionLocal()
        try:
            freshness = odds_freshness_check(session)
            stale = freshness.get("stale", 0)
            upcoming = freshness.get("upcoming", 0)

            if stale == 0:
                result.message = f"所有 {upcoming} 场比赛赔率正常"
            elif stale > upcoming * 0.5:
                result.status = "fail"
                result.message = f"{stale}/{upcoming} 场比赛赔率过期"
                self._trigger_emergency_odds_collection(result)
            else:
                result.status = "warn"
                result.message = f"{stale}/{upcoming} 场比赛赔率过期"
                self._trigger_emergency_odds_collection(result)
        except Exception as e:
            result.status = "fail"
            result.message = f"赔率检查异常: {e}"
        finally:
            session.close()

        self._report.checks.append(result)

    def _trigger_emergency_odds_collection(self, result: CheckResult) -> None:
        """触发紧急赔率采集 + zgzcw 竞彩同步"""
        actions = []
        try:
            from ingestion.odds_collector import collect_odds_tier1_primary
            from database.models import SessionLocal
            session = SessionLocal()
            try:
                collect_odds_tier1_primary(session)
                actions.append("tier1")
            finally:
                session.close()
        except Exception as e:
            fire_alert("health_daemon", "warning", f"紧急赔率采集(tier1)失败: {e}")

        try:
            from zgzcw_jc_sync import sync_jc_matches
            sync_result = sync_jc_matches(DB_PATH)
            if sync_result.get("matches", 0) > 0:
                actions.append(f"zgzcw({sync_result['matches']})")
        except Exception as e:
            fire_alert("health_daemon", "warning", f"zgzcw紧急同步失败: {e}")

        if actions:
            result.repaired = True
            result.repair_action = "triggered: " + ", ".join(actions)

    # ────────────────────────────
    # Check 3: 调度器任务健康
    # ────────────────────────────
    def _check_scheduler_jobs(self) -> None:
        result = CheckResult(name="scheduler_jobs")

        try:
            from scheduler import scheduler

            if not scheduler.running:
                result.status = "fail"
                result.message = "调度器未运行"
                fire_alert("health_daemon", "critical", "APScheduler 未运行")
                self._report.checks.append(result)
                return

            jobs = scheduler.get_jobs()
            job_info = []
            misfire_count = 0

            for job in jobs:
                next_run = job.next_run_time
                if next_run is None:
                    misfire_count += 1
                    job_info.append(f"{job.id}: NEVER")
                else:
                    job_info.append(f"{job.id}: {next_run.isoformat()}")

            if misfire_count > 0:
                result.status = "warn"
                result.message = f"{misfire_count}/{len(jobs)} 个任务无下次运行时间"
            else:
                result.message = f"{len(jobs)} 个任务正常运行"

        except Exception as e:
            result.status = "fail"
            result.message = f"调度器检查异常: {e}"

        self._report.checks.append(result)

    # ────────────────────────────
    # Check 4: 数据完整性
    # ────────────────────────────
    def _check_data_completeness(self) -> None:
        result = CheckResult(name="data_completeness")

        from database.models import SessionLocal, Match, MatchStatus, Prediction, Team
        session = SessionLocal()
        try:
            now = datetime.now(timezone.utc)
            # 只检查未来7天内的比赛，更远的比赛没有赔率/预测是正常的
            lookahead = now + timedelta(days=7)
            upcoming = session.query(Match).filter(
                Match.status.in_([MatchStatus.SCHEDULED, MatchStatus.UPCOMING]),
                Match.kickoff_at > now,
                Match.kickoff_at <= lookahead,
            ).all()

            missing_pred = 0
            missing_odds = 0
            missing_team_stats = 0

            for match in upcoming:
                # 缺少预测
                pred = session.query(Prediction).filter(
                    Prediction.match_id == match.id
                ).first()
                if not pred:
                    missing_pred += 1

                # 缺少赔率
                has_odds = match.odds_home is not None
                if not has_odds:
                    missing_odds += 1

                # 缺少球队统计
                home_team = session.query(Team).filter(Team.id == match.home_team_id).first()
                away_team = session.query(Team).filter(Team.id == match.away_team_id).first()
                if home_team and not getattr(home_team, "elo_rating", None):
                    missing_team_stats += 1
                if away_team and not getattr(away_team, "elo_rating", None):
                    missing_team_stats += 1

            total = len(upcoming)
            issues = []
            if missing_pred > 0:
                issues.append(f"缺预测:{missing_pred}")
            if missing_odds > 0:
                issues.append(f"缺赔率:{missing_odds}")
            if missing_team_stats > 0:
                issues.append(f"缺球队数据:{missing_team_stats}")

            if not issues:
                result.message = f"数据完整 ({total} 场比赛)"
            else:
                severity = "fail" if missing_pred > total * 0.5 else "warn"
                result.status = severity
                result.message = f"共 {total} 场 | " + " | ".join(issues)

                # 自动修复：触发预测锁定
                if missing_pred > 0:
                    self._trigger_prediction_lock(result)
        except Exception as e:
            result.status = "fail"
            result.message = f"数据完整性检查异常: {e}"
        finally:
            session.close()

        self._report.checks.append(result)

    def _trigger_prediction_lock(self, result: CheckResult) -> None:
        """触发缺失预测的自动锁定"""
        try:
            from scheduler import lock_predictions_job
            lock_predictions_job()
            result.repaired = True
            result.repair_action = "triggered_prediction_lock"
        except Exception as e:
            result.repair_action = f"prediction_lock_failed: {e}"
            fire_alert("health_daemon", "warning", f"自动预测锁定失败: {e}")

    # ────────────────────────────
    # Check 5: zgzcw 竞彩同步检测
    # ────────────────────────────
    def _check_zgzcw_sync(self) -> None:
        result = CheckResult(name="zgzcw_sync")
        from zgzcw_jc_sync import sync_jc_matches
        try:
            sync_result = sync_jc_matches(DB_PATH)
            m = sync_result.get("matches", 0)
            e = sync_result.get("errors", 0)
            linked = sync_result.get("issues_linked", 0)
            if m == 0 and e > 0:
                result.status = "fail"
                result.message = f"同步失败: {e} errors"
            else:
                result.message = f"同步 {m} 场, 关联期号 {linked} 场"
        except Exception as ex:
            result.status = "fail"
            result.message = f"zgzcw 同步异常: {ex}"
        self._report.checks.append(result)

    # ────────────────────────────
    # Check 6: 竞彩期号完整性
    # ────────────────────────────
    def _check_jingcai_issues(self) -> None:
        result = CheckResult(name="jingcai_issues")
        try:
            conn = sqlite3.connect(DB_PATH)
            cur = conn.cursor()
            cur.execute("SELECT status, COUNT(*) FROM jingcai_issues GROUP BY status")
            rows = cur.fetchall()
            conn.close()

            if not rows:
                result.status = "warn"
                result.message = "无竞彩期号记录"
            else:
                parts = [f"{s}={c}" for s, c in rows]
                total = sum(c for _, c in rows)
                result.message = f"共 {total} 期: " + ", ".join(parts)

                on_sale = sum(c for s, c in rows if s == "on_sale")
                if on_sale == 0:
                    result.status = "warn"
                    result.message += " (无在售期号)"
        except Exception as e:
            result.status = "fail"
            result.message = f"期号检查异常: {e}"
        self._report.checks.append(result)

    # ────────────────────────────
    # Check 7: 模型漂移检测
    # ────────────────────────────
    def _check_model_drift(self) -> None:
        result = CheckResult(name="model_drift")

        from database.models import SessionLocal, Match, MatchStatus, Prediction
        session = SessionLocal()
        try:
            # 取最近30天的已结束比赛
            cutoff = datetime.now(timezone.utc) - timedelta(days=30)
            finished = session.query(Match).filter(
                Match.status == MatchStatus.FINISHED,
                Match.actual_outcome.isnot(None),
                Match.kickoff_at >= cutoff,
            ).all()

            if len(finished) < MIN_PREDICTIONS_FOR_DRIFT:
                result.message = f"样本不足 ({len(finished)}/{MIN_PREDICTIONS_FOR_DRIFT})"
                self._report.checks.append(result)
                return

            correct = 0
            total = 0
            # 高概率失败追踪
            high_prob_failures = 0
            high_prob_total = 0

            for match in finished:
                pred = session.query(Prediction).filter(
                    Prediction.match_id == match.id,
                    Prediction.play_type == "SPF",
                ).first()
                if not pred:
                    continue

                total += 1
                probs = pred.probabilities or {}
                predicted = max(probs, key=probs.get) if probs else None
                max_prob = max(probs.values()) if probs else 0

                if predicted == match.actual_outcome:
                    correct += 1
                elif max_prob >= 0.50:
                    # 高概率预测失败（≥50%置信度但预测错误）
                    high_prob_total += 1
                    high_prob_failures += 1

            if total == 0:
                result.message = "无有效预测可分析"
                self._report.checks.append(result)
                return

            accuracy = correct / total

            if accuracy < DRIFT_ACCURACY_FLOOR:
                result.status = "fail"
                result.message = (
                    f"方向准确率 {accuracy:.1%} < {DRIFT_ACCURACY_FLOOR:.0%} 阈值 "
                    f"({correct}/{total})"
                )
                # 6小时内不重复发射同级别告警
                now = datetime.now(timezone.utc).timestamp()
                if now - self._drift_alert_cooldown > 21600:
                    fire_alert(
                        "health_daemon", "critical",
                        f"模型漂移告警: 准确率 {accuracy:.1%}，需重新校准"
                    )
                    self._drift_alert_cooldown = now
                # 触发权重重学
                self._trigger_weight_relearn(result)
            elif high_prob_total > 0 and high_prob_failures / max(high_prob_total, 1) > 0.6:
                result.status = "warn"
                result.message = (
                    f"高概率预测失败率 {high_prob_failures}/{high_prob_total}，"
                    f"总准确率 {accuracy:.1%}"
                )
                fire_alert(
                    "health_daemon", "warning",
                    f"高概率事件失败过多: {high_prob_failures}/{high_prob_total}"
                )
            else:
                result.message = f"方向准确率 {accuracy:.1%} ({correct}/{total})，正常"

        except Exception as e:
            result.status = "warn"
            result.message = f"漂移检查异常: {e}"
        finally:
            session.close()

        self._report.checks.append(result)

    def _trigger_weight_relearn(self, result: CheckResult) -> None:
        """触发完整的自愈闭环：权重重学→预测重生成→验证

        替代旧的 calibrator.fit_from_db()，走 model_audit.run_self_heal_cycle()。
        """
        try:
            from model_audit import run_self_heal_cycle
            heal_result = run_self_heal_cycle(reason="health_daemon_drift_detected")
            if heal_result.get("status") == "completed":
                result.repaired = True
                result.repair_action = "self_heal_cycle_completed"
            else:
                result.repair_action = f"self_heal_cycle_status={heal_result.get('status', 'unknown')}"
                fire_alert("health_daemon", "warning",
                           f"自愈闭环未完成: {heal_result.get('status', 'unknown')}")
        except Exception as e:
            result.repair_action = f"weight_relearn_failed: {e}"
            fire_alert("health_daemon", "critical", f"权重重学失败: {e}")

    # ────────────────────────────
    # Check 8: 连续失败检测
    # ────────────────────────────
    def _check_consecutive_failures(self) -> None:
        result = CheckResult(name="consecutive_failures")

        try:
            alerts = get_active_alerts()
            from collections import Counter
            failure_sources = Counter()
            cutoff = datetime.now(timezone.utc).timestamp() - 3600

            for a in alerts:
                if a.get("ts", 0) < cutoff:
                    continue
                # 跳过 health_daemon 自身产生的告警，避免自激回路
                if a.get("source", "") == "health_daemon":
                    continue
                msg = a.get("message", "").lower()
                if "failed" in msg or "error" in msg or "失败" in msg:
                    failure_sources[a.get("source", "unknown")] += 1

            problematic = {k: v for k, v in failure_sources.items() if v >= MAX_CONSECUTIVE_JOB_FAILURES}

            if problematic:
                result.status = "warn"
                result.message = f"连续失败源: {dict(problematic)}"
                for source in problematic:
                    check_consecutive_failures(source, MAX_CONSECUTIVE_JOB_FAILURES)
            else:
                result.message = "无连续失败"
        except Exception as e:
            result.status = "warn"
            result.message = f"失败检查异常: {e}"

        self._report.checks.append(result)

    # ────────────────────────────
    # Check 9: 备份新鲜度
    # ────────────────────────────
    def _check_backup_freshness(self) -> None:
        result = CheckResult(name="backup_freshness")

        import glob
        backups = sorted(glob.glob(f"{BACKUP_DIR}/db_*.sqlite"), reverse=True)

        if not backups:
            result.status = "warn"
            result.message = "无数据库备份"
            self._trigger_backup(result)
            self._report.checks.append(result)
            return

        # 检查最新备份时间
        latest = backups[0]
        mtime = os.path.getmtime(latest)
        age_hours = (datetime.now().timestamp() - mtime) / 3600

        if age_hours > 48:
            result.status = "warn"
            result.message = f"最新备份已 {age_hours:.0f} 小时前"
            self._trigger_backup(result)
        else:
            result.message = f"最新备份 {age_hours:.1f} 小时前 ({len(backups)} 个)"

        self._report.checks.append(result)

    def _trigger_backup(self, result: CheckResult) -> None:
        """触发紧急数据库备份"""
        try:
            from scheduler import backup_database_job
            backup_database_job()
            result.repaired = True
            result.repair_action = "triggered_emergency_backup"
        except Exception as e:
            result.repair_action = f"backup_failed: {e}"
            fire_alert("health_daemon", "critical", f"紧急备份失败: {e}")

    # ────────────────────────────
    # 汇总
    # ────────────────────────────
    def _determine_overall(self) -> None:
        has_fail = any(c.status == "fail" for c in self._report.checks)
        has_warn = any(c.status == "warn" for c in self._report.checks)

        if has_fail:
            self._report.overall = "critical"
        elif has_warn:
            self._report.overall = "degraded"
        else:
            self._report.overall = "ok"

    def _persist_report(self) -> None:
        """将健康报告写入磁盘供 API 读取"""
        import json
        os.makedirs(os.path.dirname(HEALTH_FILE), exist_ok=True)
        with open(HEALTH_FILE, "w") as f:
            json.dump(self._report.to_dict(), f, indent=2, ensure_ascii=False)

        status_emoji = {"ok": "✅", "degraded": "⚠️", "critical": "🔴"}
        logger.info(
            f"[health] {status_emoji.get(self._report.overall, '?')} "
            f"{self._report.overall.upper()} | "
            + " | ".join(
                f"{c.name}={c.status}" for c in self._report.checks
            )
        )


# ────────────────────────────
# Scheduler 入口
# ────────────────────────────
_health_critical_cooldown: float = 0  # 模块级：系统整体 critical 告警冷却


def health_check_job() -> None:
    """定时任务入口：每 10 分钟执行一次自检"""
    daemon = HealthDaemon()
    report = daemon.run_all_checks()

    if report.overall == "critical":
        global _health_critical_cooldown
        now = datetime.now(timezone.utc).timestamp()
        if now - _health_critical_cooldown > 21600:
            fire_alert("health_daemon", "critical",
                       f"系统健康检查失败: "
                       + ", ".join(c.message for c in report.checks if c.status == "fail"))
            _health_critical_cooldown = now


def get_latest_health() -> dict:
    """读取最新健康报告（供 API 使用）"""
    import json
    if not os.path.exists(HEALTH_FILE):
        return {"overall": "unknown", "checks": [], "timestamp": None}
    try:
        with open(HEALTH_FILE, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return {"overall": "unknown", "checks": [], "timestamp": None}
