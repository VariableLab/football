"""
Model Audit — 每日复盘 + 自愈闭环

三段闭环：审计(检测漂移) → 重学(weight_learner.learn_all) → 重生成(regenerate)

设计原则:
- 高概率事件失误权重更大（用户更依赖高置信度预测）
- 漂移超过阈值才触发全量重学（避免无意义的频繁重算）
- 自愈过程是幂等的（并发锁 + 状态文件）
- 每日复盘写入磁盘；每周或漂移时触发完整闭环

用法:
  from model_audit import daily_audit_job, weekly_audit_job, self_heal_job
  # 调度器会自动调用，也可手动执行
"""

import json
import os
import threading
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from utils.logger import get_logger
from alert_manager import fire_alert

logger = get_logger("model_audit")

AUDIT_DIR = "./data/model_audit"
os.makedirs(AUDIT_DIR, exist_ok=True)

# 漂移阈值
DRIFT_THRESHOLD = 0.03          # 方向准确率与期望差距超3%触发自愈
BRIER_DRIFT_THRESHOLD = 0.01    # Brier恶化超0.01触发自愈
MIN_AUDIT_SAMPLES = 30          # 至少30场才做漂移判断
EXPECTED_ACCURACY = 0.51        # 基线(30K场全量准确率)

# 自愈状态文件(用于幂等和防并发)
SELF_HEAL_STATE_PATH = os.path.join(AUDIT_DIR, "self_heal_state.json")

# 全局自愈锁(防止并发执行)
_self_heal_lock = threading.Lock()


@dataclass(frozen=True)
class AuditEntry:
    match_id: int
    match_code: str
    predicted: str
    actual: str
    confidence: float
    correct: bool
    prob_home: float
    prob_draw: float
    prob_away: float
    is_high_prob: bool


@dataclass
class AuditReport:
    date: str
    total: int = 0
    correct: int = 0
    direction_accuracy: float = 0.0
    high_prob_total: int = 0
    high_prob_correct: int = 0
    high_prob_accuracy: float = 0.0
    low_prob_total: int = 0
    low_prob_correct: int = 0
    low_prob_accuracy: float = 0.0
    brier_score: float = 0.0
    per_outcome: Dict[str, Dict] = field(default_factory=dict)
    entries: List[AuditEntry] = field(default_factory=list)
    weight_adjustment: Optional[Dict] = None
    self_heal_triggered: bool = False
    self_heal_result: Optional[Dict] = None

    def to_dict(self) -> dict:
        return {
            "date": self.date,
            "total": self.total,
            "correct": self.correct,
            "direction_accuracy": round(self.direction_accuracy, 4),
            "high_prob_total": self.high_prob_total,
            "high_prob_correct": self.high_prob_correct,
            "high_prob_accuracy": round(self.high_prob_accuracy, 4),
            "low_prob_total": self.low_prob_total,
            "low_prob_correct": self.low_prob_correct,
            "low_prob_accuracy": round(self.low_prob_accuracy, 4),
            "brier_score": round(self.brier_score, 4),
            "per_outcome": self.per_outcome,
            "weight_adjustment": self.weight_adjustment,
            "self_heal_triggered": self.self_heal_triggered,
            "self_heal_result": self.self_heal_result,
        }


# ────────────────────────────
# 审计器
# ────────────────────────────

class ModelAuditor:
    """每日模型复盘 + 自愈闭环"""

    def run_daily_audit(self, days_back: int = 1) -> Optional[AuditReport]:
        """对最近 days_back 天的已结束比赛做复盘"""
        from database.models import SessionLocal, Match, MatchStatus, Prediction

        session = SessionLocal()
        try:
            cutoff = datetime.now(timezone.utc) - timedelta(days=days_back)
            finished = session.query(Match).filter(
                Match.status == MatchStatus.FINISHED,
                Match.actual_outcome.isnot(None),
                Match.kickoff_at >= cutoff,
            ).all()

            if not finished:
                logger.info("[audit] No finished matches to audit")
                return None

            entries: List[AuditEntry] = []
            brier_sum = 0.0

            for match in finished:
                pred = session.query(Prediction).filter(
                    Prediction.match_id == match.id,
                    Prediction.play_type == "SPF",
                ).first()
                if not pred:
                    continue

                probs = pred.probabilities if isinstance(pred.probabilities, dict) else json.loads(pred.probabilities)
                if not probs:
                    continue

                predicted = max(probs, key=probs.get)
                max_prob = max(probs.values())
                actual = match.actual_outcome
                correct = predicted == actual

                for sel in ["home", "draw", "away"]:
                    p = probs.get(sel, 0)
                    o = 1.0 if sel == actual else 0.0
                    brier_sum += (p - o) ** 2

                is_high_prob = max_prob >= 0.50

                entries.append(AuditEntry(
                    match_id=match.id,
                    match_code=match.match_code,
                    predicted=predicted,
                    actual=actual,
                    confidence=max_prob,
                    correct=correct,
                    prob_home=probs.get("home", 0),
                    prob_draw=probs.get("draw", 0),
                    prob_away=probs.get("away", 0),
                    is_high_prob=is_high_prob,
                ))

            if not entries:
                logger.info("[audit] No predictions found for finished matches")
                return None

            report = self._build_report(entries, brier_sum)
            self._check_drift(report)
            self._persist_report(report)

            return report
        finally:
            session.close()

    def run_weekly_deep_audit(self) -> Optional[AuditReport]:
        """深度周复盘：7天数据 + 必要时触发自愈"""
        report = self.run_daily_audit(days_back=7)
        if report and report.total >= MIN_AUDIT_SAMPLES:
            drift = abs(report.direction_accuracy - EXPECTED_ACCURACY)
            brier_bad = report.brier_score > 0.22
            if drift > DRIFT_THRESHOLD or brier_bad:
                result = run_self_heal_cycle(reason=f"weekly_audit_drift={drift:.3f}")
                report.self_heal_triggered = True
                report.self_heal_result = result
                self._persist_report(report)
        return report

    def _build_report(self, entries: List[AuditEntry], brier_sum: float) -> AuditReport:
        total = len(entries)
        correct = sum(1 for e in entries if e.correct)
        high = [e for e in entries if e.is_high_prob]
        low = [e for e in entries if not e.is_high_prob]

        per_outcome: Dict[str, Dict] = {}
        for outcome in ["home", "draw", "away"]:
            group = [e for e in entries if e.actual == outcome]
            if group:
                group_correct = sum(1 for e in group if e.correct)
                per_outcome[outcome] = {
                    "total": len(group),
                    "correct": group_correct,
                    "accuracy": round(group_correct / len(group), 4),
                    "avg_confidence": round(sum(e.confidence for e in group) / len(group), 4),
                }

        report = AuditReport(
            date=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            total=total,
            correct=correct,
            direction_accuracy=correct / total if total > 0 else 0,
            high_prob_total=len(high),
            high_prob_correct=sum(1 for e in high if e.correct),
            low_prob_total=len(low),
            low_prob_correct=sum(1 for e in low if e.correct),
            brier_score=brier_sum / (total * 3) if total > 0 else 0,
            per_outcome=per_outcome,
            entries=entries,
        )

        report.high_prob_accuracy = (
            report.high_prob_correct / report.high_prob_total
            if report.high_prob_total > 0 else 0
        )
        report.low_prob_accuracy = (
            report.low_prob_correct / report.low_prob_total
            if report.low_prob_total > 0 else 0
        )

        return report

    def _check_drift(self, report: AuditReport) -> None:
        """检测准确率漂移，必要时触发自愈"""
        if report.total < MIN_AUDIT_SAMPLES:
            logger.info(f"[audit] 样本不足 ({report.total}/{MIN_AUDIT_SAMPLES})，跳过漂移检测")
            return

        # 高概率事件准确率太低 → 告警
        if report.high_prob_total >= 10 and report.high_prob_accuracy < 0.50:
            fire_alert(
                "model_audit", "critical",
                f"高概率预测准确率仅 {report.high_prob_accuracy:.1%} "
                f"({report.high_prob_correct}/{report.high_prob_total})，需重新校准"
            )

        drift = report.direction_accuracy - EXPECTED_ACCURACY

        if abs(drift) > DRIFT_THRESHOLD:
            logger.warning(
                f"[audit] 方向准确率漂移: {report.direction_accuracy:.1%} "
                f"(期望 {EXPECTED_ACCURACY:.0%}, 偏差 {drift:+.1%})"
            )
            if report.brier_score > 0.22:
                logger.warning("[audit] Brier 亦恶化，触发自愈闭环")
                result = run_self_heal_cycle(reason=f"daily_drift={drift:.3f}_brier={report.brier_score:.4f}")
                report.self_heal_triggered = True
                report.self_heal_result = result
        elif report.brier_score - 0.20 > BRIER_DRIFT_THRESHOLD:
            logger.warning(f"[audit] Brier恶化: {report.brier_score:.4f} > 0.21，触发自愈")
            result = run_self_heal_cycle(reason=f"brier_drift={report.brier_score:.4f}")
            report.self_heal_triggered = True
            report.self_heal_result = result
        else:
            logger.info(
                f"[audit] 方向准确率 {report.direction_accuracy:.1%}，Brier {report.brier_score:.4f}，正常"
            )

    def _persist_report(self, report: AuditReport) -> None:
        """将复盘报告写入磁盘"""
        date_str = report.date
        path = f"{AUDIT_DIR}/audit_{date_str}.json"

        data = report.to_dict()
        with open(path, "w") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

        logger.info(
            f"[audit] 复盘报告: 方向={report.direction_accuracy:.1%} "
            f"高概率={report.high_prob_accuracy:.1%} "
            f"Brier={report.brier_score:.4f}"
        )

    @staticmethod
    def get_latest_reports(n: int = 7) -> List[dict]:
        """获取最近 n 天的复盘报告"""
        reports = []
        files = sorted(
            [f for f in os.listdir(AUDIT_DIR) if f.startswith("audit_") and f.endswith(".json")],
            reverse=True,
        )
        for fname in files[:n]:
            try:
                with open(os.path.join(AUDIT_DIR, fname), "r") as f:
                    reports.append(json.load(f))
            except (json.JSONDecodeError, IOError):
                continue
        return reports


# ────────────────────────────
# 自愈闭环：审计 → 重学 → 重生成
# ────────────────────────────

def _load_self_heal_state() -> dict:
    """读取自愈状态文件"""
    if os.path.exists(SELF_HEAL_STATE_PATH):
        try:
            with open(SELF_HEAL_STATE_PATH, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {"status": "idle", "last_run": None, "last_result": None}


def _save_self_heal_state(state: dict) -> None:
    """写入自愈状态文件"""
    with open(SELF_HEAL_STATE_PATH, "w") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)


def run_self_heal_cycle(reason: str = "manual") -> dict:
    """
    自愈闭环主入口：审计 → 权重重学 → 预测重生成

    返回: {
        "status": "success"|"skipped"|"failed",
        "reason": str,
        "weight_learn_results": dict|None,
        "regenerate_count": int|None,
        "duration_seconds": float,
        "started_at": str,
    }
    """
    started_at = datetime.now(timezone.utc).isoformat()

    # 幂等检查：获取锁
    acquired = _self_heal_lock.acquire(blocking=False)
    if not acquired:
        logger.warning("[self-heal] 另一个自愈进程正在运行，跳过")
        return {
            "status": "skipped",
            "reason": "another_self_heal_running",
            "started_at": started_at,
        }

    # 冷却期检查：距上次自愈不足6小时则跳过
    prev_state = _load_self_heal_state()
    if prev_state.get("status") == "running":
        # 检查是否由于崩溃导致的僵尸锁（超过4小时认为失效）
        prev_start = prev_state.get("started_at")
        if prev_start:
            try:
                prev_dt = datetime.fromisoformat(prev_start)
                if (datetime.now(timezone.utc) - prev_dt) > timedelta(hours=4):
                    logger.warning(f"[self-heal] 发现过期僵尸锁(开始于{prev_start})，强制解锁并继续")
                else:
                    logger.warning("[self-heal] 上次自愈仍在运行，跳过")
                    _self_heal_lock.release()
                    return {"status": "skipped", "reason": "previous_still_running", "started_at": started_at}
            except (ValueError, TypeError):
                pass
        else:
            logger.warning("[self-heal] 上次自愈仍在运行(无时间戳)，跳过")
            _self_heal_lock.release()
            return {"status": "skipped", "reason": "previous_still_running_no_ts", "started_at": started_at}

    last_run = prev_state.get("last_run")
    if last_run:
        try:
            last_dt = datetime.fromisoformat(last_run)
            cooldown = datetime.now(timezone.utc) - last_dt
            if cooldown < timedelta(hours=6):
                logger.info(f"[self-heal] 冷却中(上次运行{cooldown}前)，跳过")
                _self_heal_lock.release()
                return {"status": "skipped", "reason": "cooldown", "started_at": started_at}
        except (ValueError, TypeError):
            pass

    try:
        _save_self_heal_state({"status": "running", "started_at": started_at, "reason": reason})

        logger.info(f"[self-heal] ========== 开始自愈闭环 ({reason}) ==========")
        t0 = datetime.now(timezone.utc)

        # ── 第1步：权重重学 ──
        weight_results = _step_weight_learn()
        if weight_results is None:
            _save_self_heal_state({
                "status": "failed",
                "last_run": started_at,
                "last_result": {"status": "failed", "step": "weight_learn"},
                "reason": reason,
            })
            return _make_result("failed", reason, started_at, t0, step_failed="weight_learn")

        # ── 第2步：预测重生成 ──
        regen_count = _step_regenerate()
        if regen_count is None:
            _save_self_heal_state({
                "status": "failed",
                "last_run": started_at,
                "last_result": {"status": "failed", "step": "regenerate"},
                "reason": reason,
            })
            return _make_result("failed", reason, started_at, t0, step_failed="regenerate")

        # ── 第3步：验证新权重效果 ──
        validation = _step_validate()

        duration = (datetime.now(timezone.utc) - t0).total_seconds()
        result = _make_result("success", reason, started_at, t0,
                              weight_results=weight_results,
                              regen_count=regen_count,
                              validation=validation,
                              duration=duration)

        _save_self_heal_state({
            "status": "idle",
            "last_run": started_at,
            "last_result": result,
            "reason": reason,
        })

        logger.info(
            f"[self-heal] ========== 自愈完成 ========== "
            f"权重={len(weight_results)}组, 重生成={regen_count}场, "
            f"耗时={duration:.0f}s, "
            f"验证: acc={validation.get('accuracy', 0):.4f} brier={validation.get('brier', 0):.4f}"
        )

        return result

    except Exception as e:
        logger.error(f"[self-heal] 自愈闭环异常: {e}", exc_info=True)
        fire_alert("model_audit", "critical", f"自愈闭环失败: {e}")
        _save_self_heal_state({
            "status": "failed",
            "last_run": started_at,
            "last_result": {"status": "failed", "error": str(e)},
            "reason": reason,
        })
        return _make_result("failed", reason, started_at, datetime.now(timezone.utc), error=str(e))

    finally:
        _self_heal_lock.release()


def _step_weight_learn() -> Optional[Dict]:
    """第1步：从全量历史数据回归学习最优融合权重"""
    from database.models import SessionLocal
    from weight_learner import WeightLearner

    db = SessionLocal()
    try:
        learner = WeightLearner(db)
        logger.info("[self-heal] Step 1/3: 权重重学开始 (全量30K+样本)...")
        results = learner.learn_all(metric="brier")

        if not results:
            logger.error("[self-heal] 权重重学返回空结果")
            return None

        summary = {}
        for key, lw in results.items():
            summary[key] = {
                "weights": lw.weights,
                "brier": round(lw.metric_value, 4),
                "n": lw.sample_size,
            }

        logger.info(f"[self-heal] Step 1/3: 权重重学完成，{len(results)}组权重")
        return summary

    except Exception as e:
        logger.error(f"[self-heal] 权重重学失败: {e}", exc_info=True)
        return None
    finally:
        db.close()


def _step_regenerate() -> Optional[int]:
    """第2步：用新权重重新生成所有已完成比赛预测"""
    from database.models import SessionLocal, Match, MatchStatus
    from regenerate_predictions import regenerate_matches

    db = SessionLocal()
    try:
        logger.info("[self-heal] Step 2/3: 预测重生成开始...")
        matches = db.query(Match).filter(Match.status == MatchStatus.FINISHED).all()
        count = regenerate_matches(db, matches, label="[self-heal]")
        logger.info(f"[self-heal] Step 2/3: 预测重生成完成，{count}场")

        # 也刷新 SCHEDULED 比赛
        scheduled = db.query(Match).filter(Match.status == MatchStatus.SCHEDULED).all()
        if scheduled:
            sc = regenerate_matches(db, scheduled, label="[self-heal-scheduled]")
            logger.info(f"[self-heal] Step 2/3: SCHEDULED重生成完成，{sc}场")
            count += sc

        return count

    except Exception as e:
        logger.error(f"[self-heal] 预测重生成失败: {e}", exc_info=True)
        return None
    finally:
        db.close()


def _step_validate() -> dict:
    """第3步：快速验证新权重效果（采样500场）"""
    import sqlite3

    conn = sqlite3.connect("database.sqlite")
    c = conn.cursor()

    c.execute("""
        SELECT p.probabilities, m.actual_outcome
        FROM predictions p
        JOIN matches m ON p.match_id = m.id
        WHERE p.play_type = 'SPF'
        AND m.status = 'FINISHED'
        AND m.actual_outcome IS NOT NULL
        ORDER BY RANDOM()
        LIMIT 500
    """)
    rows = c.fetchall()
    conn.close()

    if not rows:
        return {"accuracy": 0, "brier": 0, "sample": 0}

    correct = 0
    total = len(rows)
    briers = []
    for prob_str, actual in rows:
        probs = json.loads(prob_str) if isinstance(prob_str, str) else prob_str
        if not probs:
            continue
        pred = max(probs, key=probs.get)
        if pred == actual:
            correct += 1
        for k in ["home", "draw", "away"]:
            briers.append((probs.get(k, 0.33) - (1 if actual == k else 0)) ** 2)

    accuracy = correct / total
    brier = sum(briers) / len(briers) if briers else 0

    logger.info(f"[self-heal] Step 3/3: 验证采样{total}场 → acc={accuracy:.4f}, brier={brier:.4f}")
    return {"accuracy": round(accuracy, 4), "brier": round(brier, 4), "sample": total}


def _make_result(
    status: str,
    reason: str,
    started_at: str,
    t0: datetime,
    weight_results: Optional[Dict] = None,
    regen_count: Optional[int] = None,
    validation: Optional[dict] = None,
    duration: float = 0,
    step_failed: Optional[str] = None,
    error: Optional[str] = None,
) -> dict:
    """构造标准返回字典"""
    result = {
        "status": status,
        "reason": reason,
        "started_at": started_at,
        "duration_seconds": round(duration, 1),
    }
    if weight_results:
        result["weight_learn_results"] = weight_results
    if regen_count is not None:
        result["regenerate_count"] = regen_count
    if validation:
        result["validation"] = validation
    if step_failed:
        result["step_failed"] = step_failed
    if error:
        result["error"] = error
    return result


# ────────────────────────────
# Scheduler 入口
# ────────────────────────────

def daily_audit_job() -> None:
    """每日复盘定时任务"""
    auditor = ModelAuditor()
    report = auditor.run_daily_audit(days_back=1)
    if report and report.total > 0:
        logger.info(
            f"[daily-audit] {report.total} 场 | "
            f"方向={report.direction_accuracy:.1%} | "
            f"高概率={report.high_prob_accuracy:.1%} | "
            f"Brier={report.brier_score:.4f}"
        )


def weekly_audit_job() -> None:
    """每周深度复盘定时任务（含自愈闭环）"""
    auditor = ModelAuditor()
    report = auditor.run_weekly_deep_audit()
    if report and report.total > 0:
        heal_info = ""
        if report.self_heal_triggered:
            heal_info = f" | 自愈={report.self_heal_result.get('status', '?') if report.self_heal_result else '?'}"
        logger.info(
            f"[weekly-audit] 7天 {report.total} 场 | "
            f"方向={report.direction_accuracy:.1%} | "
            f"高概率={report.high_prob_accuracy:.1%}{heal_info}"
        )


def self_heal_job() -> dict:
    """独立自愈任务入口（可由调度器直接调用）"""
    return run_self_heal_cycle(reason="scheduled")
