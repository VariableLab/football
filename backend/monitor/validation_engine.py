"""
实时验证引擎 — 赛后对比预测 vs 实际结果

用法：
    from monitor.validation_engine import ValidationEngine, MatchValidator
    
    # 单场比赛验证
    result = MatchValidator.validate_match(db, match_id)
    
    # 批量验证（热身赛/友谊赛）
    report = ValidationEngine.run_validation(db, match_type="friendly")
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any
from datetime import datetime

from sqlalchemy.orm import Session

from database.models import Match, MatchStatus, Prediction, PlayType, AccuracySnapshot


# ────────────────────────────
# 单场比赛验证结果
# ────────────────────────────
@dataclass
class SingleValidationResult:
    match_id: int
    match_code: str
    home_team: str
    away_team: str
    
    actual_outcome: str          # home / draw / away
    predicted_outcome: str       # 概率最高的预测方向
    
    # SPF 预测概率
    prob_home: float
    prob_draw: float
    prob_away: float
    
    # 验证指标
    direction_correct: bool      # 方向是否猜对
    brier_score: float           # Brier Score (越低越好)
    log_loss: float              # Log Loss
    max_prob: float              # 预测的最高概率
    confidence: str              # high / medium / low
    
    # 比分验证（如果 predicted_score 存在）
    actual_score: Optional[str] = None
    predicted_top_score: Optional[str] = None
    score_correct: bool = False
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "match_id": self.match_id,
            "match_code": self.match_code,
            "home_team": self.home_team,
            "away_team": self.away_team,
            "actual_outcome": self.actual_outcome,
            "predicted_outcome": self.predicted_outcome,
            "probabilities": {
                "home": round(self.prob_home, 4),
                "draw": round(self.prob_draw, 4),
                "away": round(self.prob_away, 4),
            },
            "direction_correct": self.direction_correct,
            "brier_score": round(self.brier_score, 4),
            "log_loss": round(self.log_loss, 4),
            "max_prob": round(self.max_prob, 4),
            "confidence": self.confidence,
            "actual_score": self.actual_score,
            "predicted_top_score": self.predicted_top_score,
            "score_correct": self.score_correct,
        }


# ────────────────────────────
# 汇总验证报告
# ────────────────────────────
@dataclass
class ValidationReport:
    total_matches: int
    finished_matches: int
    validated_matches: int       # 有预测+有结果的场次
    
    # 方向准确率
    direction_accuracy: float
    high_conf_accuracy: float
    medium_conf_accuracy: float
    low_conf_accuracy: float
    
    # 概率校准度
    avg_brier_score: float
    avg_log_loss: float
    avg_max_prob: float
    
    # 按玩法统计
    by_play_type: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    
    # 逐场明细
    match_results: List[SingleValidationResult] = field(default_factory=list)
    
    generated_at: datetime = field(default_factory=datetime.utcnow)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "summary": {
                "total_matches": self.total_matches,
                "finished_matches": self.finished_matches,
                "validated_matches": self.validated_matches,
                "direction_accuracy": round(self.direction_accuracy, 4),
                "high_conf_accuracy": round(self.high_conf_accuracy, 4),
                "medium_conf_accuracy": round(self.medium_conf_accuracy, 4),
                "low_conf_accuracy": round(self.low_conf_accuracy, 4),
                "avg_brier_score": round(self.avg_brier_score, 4),
                "avg_log_loss": round(self.avg_log_loss, 4),
                "avg_max_prob": round(self.avg_max_prob, 4),
            },
            "by_play_type": self.by_play_type,
            "matches": [m.to_dict() for m in self.match_results],
            "generated_at": self.generated_at.isoformat(),
        }


# ────────────────────────────
# 核心工具函数
# ────────────────────────────
def brier_score(prob_true: float, outcome: int) -> float:
    """Brier Score: (prob - outcome)^2"""
    return (prob_true - outcome) ** 2


def compute_brier(probs: Dict[str, float], actual: str) -> float:
    """对3路结果计算平均 Brier Score"""
    return sum(
        brier_score(probs.get(k, 0), 1 if actual == k else 0)
        for k in ["home", "draw", "away"]
    ) / 3.0


def compute_log_loss(probs: Dict[str, float], actual: str) -> float:
    """计算对数损失（加平滑）"""
    prob = probs.get(actual, 1e-6)
    return -math.log(max(prob, 1e-6))


def predicted_outcome(probs: Dict[str, float]) -> str:
    """返回概率最高的结果"""
    return max(probs, key=probs.get)


# ────────────────────────────
# 单场比赛验证器
# ────────────────────────────
class MatchValidator:
    """验证单场比赛的预测结果"""
    
    @classmethod
    def validate_match(cls, db: Session, match_id: int) -> Optional[SingleValidationResult]:
        match = db.query(Match).filter(Match.id == match_id).first()
        if not match:
            return None
        
        if match.status != MatchStatus.FINISHED:
            return None
        
        if match.actual_outcome is None:
            return None
        
        # 获取 SPF 预测
        pred = db.query(Prediction).filter(
            Prediction.match_id == match_id,
            Prediction.play_type == PlayType.SPF
        ).first()
        
        if not pred:
            return None
        
        probs = pred.probabilities
        actual = match.actual_outcome
        pred_outcome = predicted_outcome(probs)
        
        # 计算指标
        bs = compute_brier(probs, actual)
        ll = compute_log_loss(probs, actual)
        max_prob = max(probs.values())
        
        # 置信度（从 match.confidence 或根据概率推断）
        confidence = "medium"
        if max_prob >= 0.60:
            confidence = "high"
        elif max_prob < 0.45:
            confidence = "low"
        
        # 比分验证
        actual_score = None
        predicted_top_score = None
        score_correct = False
        if match.actual_home_goals is not None and match.actual_away_goals is not None:
            actual_score = f"{match.actual_home_goals}:{match.actual_away_goals}"
            score_pred = db.query(Prediction).filter(
                Prediction.match_id == match_id,
                Prediction.play_type == PlayType.SCORE
            ).first()
            if score_pred:
                top_score = max(score_pred.probabilities.items(), key=lambda x: x[1])
                predicted_top_score = top_score[0]
                score_correct = (predicted_top_score == actual_score)
        
        return SingleValidationResult(
            match_id=match.id,
            match_code=match.match_code,
            home_team=match.home_team.name,
            away_team=match.away_team.name,
            actual_outcome=actual,
            predicted_outcome=pred_outcome,
            prob_home=probs.get("home", 0),
            prob_draw=probs.get("draw", 0),
            prob_away=probs.get("away", 0),
            direction_correct=(pred_outcome == actual),
            brier_score=bs,
            log_loss=ll,
            max_prob=max_prob,
            confidence=confidence,
            actual_score=actual_score,
            predicted_top_score=predicted_top_score,
            score_correct=score_correct,
        )


# ────────────────────────────
# 批量验证引擎
# ────────────────────────────
class ValidationEngine:
    """批量验证已完成比赛的预测准确性"""
    
    @classmethod
    def run_validation(
        cls,
        db: Session,
        match_type: Optional[str] = None,
        limit: int = 500
    ) -> ValidationReport:
        """
        对所有已完成且有预测的比赛进行验证。
        
        Args:
            match_type: 筛选比赛类型 (world_cup / friendly / warm_up)
            limit: 最大验证场次
        """
        q = db.query(Match).filter(Match.status == MatchStatus.FINISHED)
        if match_type:
            q = q.filter(Match.match_type == match_type)
        
        matches = q.order_by(Match.kickoff_at.desc()).limit(limit).all()
        
        results: List[SingleValidationResult] = []
        for match in matches:
            r = MatchValidator.validate_match(db, match.id)
            if r:
                results.append(r)
        
        if not results:
            return ValidationReport(
                total_matches=len(matches),
                finished_matches=len(matches),
                validated_matches=0,
                direction_accuracy=0.0,
                high_conf_accuracy=0.0,
                medium_conf_accuracy=0.0,
                low_conf_accuracy=0.0,
                avg_brier_score=0.0,
                avg_log_loss=0.0,
                avg_max_prob=0.0,
            )
        
        # 汇总统计
        corrects = [r.direction_correct for r in results]
        briers = [r.brier_score for r in results]
        log_losses = [r.log_loss for r in results]
        max_probs = [r.max_prob for r in results]
        
        # 按置信度分组
        high_conf = [r for r in results if r.confidence == "high"]
        medium_conf = [r for r in results if r.confidence == "medium"]
        low_conf = [r for r in results if r.confidence == "low"]
        
        def _accuracy(items: List[SingleValidationResult]) -> float:
            if not items:
                return 0.0
            return sum(1 for x in items if x.direction_correct) / len(items)
        
        # 按玩法统计（SPF 为主）
        by_play = {
            "spf": {
                "matches": len(results),
                "accuracy": round(_accuracy(results), 4),
                "avg_brier": round(sum(briers) / len(briers), 4),
            }
        }
        
        report = ValidationReport(
            total_matches=len(matches),
            finished_matches=len(matches),
            validated_matches=len(results),
            direction_accuracy=_accuracy(results),
            high_conf_accuracy=_accuracy(high_conf),
            medium_conf_accuracy=_accuracy(medium_conf),
            low_conf_accuracy=_accuracy(low_conf),
            avg_brier_score=sum(briers) / len(briers),
            avg_log_loss=sum(log_losses) / len(log_losses),
            avg_max_prob=sum(max_probs) / len(max_probs),
            by_play_type=by_play,
            match_results=results,
        )

        # 持久化到 AccuracySnapshot
        cls._save_snapshot(db, report, snapshot_type="daily", stage="all")

        return report

    @classmethod
    def _save_snapshot(
        cls,
        db: Session,
        report: ValidationReport,
        snapshot_type: str = "daily",
        stage: str = "all",
        weights: Optional[Dict[str, float]] = None,
    ) -> None:
        """将验证报告保存为 AccuracySnapshot"""
        from datetime import datetime, timedelta
        try:
            snapshots = []
            period_end = datetime.utcnow()
            period_start = period_end - timedelta(days=7) if snapshot_type == "weekly" else period_end - timedelta(days=1)

            # direction_accuracy
            snapshots.append(AccuracySnapshot(
                snapshot_type=snapshot_type,
                metric="direction_accuracy",
                value=report.direction_accuracy,
                sample_size=report.validated_matches,
                weights=weights,
                stage=stage,
                period_start=period_start,
                period_end=period_end,
                notes=f"high_conf={report.high_conf_accuracy:.3f}, medium_conf={report.medium_conf_accuracy:.3f}",
            ))
            # brier
            snapshots.append(AccuracySnapshot(
                snapshot_type=snapshot_type,
                metric="brier",
                value=report.avg_brier_score,
                sample_size=report.validated_matches,
                weights=weights,
                stage=stage,
                period_start=period_start,
                period_end=period_end,
            ))
            # log_loss
            snapshots.append(AccuracySnapshot(
                snapshot_type=snapshot_type,
                metric="log_loss",
                value=report.avg_log_loss,
                sample_size=report.validated_matches,
                weights=weights,
                stage=stage,
                period_start=period_start,
                period_end=period_end,
            ))
            for snap in snapshots:
                db.add(snap)
            db.commit()
        except Exception as e:
            db.rollback()
            print(f"⚠️ AccuracySnapshot save failed: {e}")

    @classmethod
    def validate_friendly_only(cls, db: Session) -> ValidationReport:
        """仅验证热身赛/友谊赛（世界杯前实时验证用）"""
        return cls.run_validation(db, match_type="friendly")

    @classmethod
    def calibration_curve(cls, db: Session, n_bins: int = 10) -> Dict[str, Any]:
        """
        计算概率校准曲线（可靠性图）。
        将预测概率分成 n_bins 个桶，计算每个桶内实际正确率。
        完美校准：实际正确率 = 预测概率平均值。
        """
        matches = db.query(Match).filter(
            Match.status == MatchStatus.FINISHED,
            Match.actual_outcome.isnot(None),
        ).all()

        bins = [[] for _ in range(n_bins)]

        for match in matches:
            pred = db.query(Prediction).filter(
                Prediction.match_id == match.id,
                Prediction.play_type == PlayType.SPF,
            ).first()
            if not pred:
                continue

            probs = pred.probabilities
            pred_out = predicted_outcome(probs)
            max_prob = max(probs.values())
            correct = (pred_out == match.actual_outcome)

            bin_idx = min(int(max_prob * n_bins), n_bins - 1)
            bins[bin_idx].append((max_prob, correct))

        curve = []
        for i, items in enumerate(bins):
            if not items:
                continue
            avg_prob = sum(p for p, _ in items) / len(items)
            actual_rate = sum(1 for _, c in items if c) / len(items)
            curve.append({
                "bin": f"{i/n_bins:.1f}-{(i+1)/n_bins:.1f}",
                "avg_predicted_prob": round(avg_prob, 3),
                "actual_accuracy": round(actual_rate, 3),
                "sample_size": len(items),
            })

        # 计算校准误差 (ECE)
        total = sum(len(b) for b in bins if b)
        ece = 0
        for items in bins:
            if not items:
                continue
            avg_prob = sum(p for p, _ in items) / len(items)
            actual_rate = sum(1 for _, c in items if c) / len(items)
            ece += len(items) * abs(avg_prob - actual_rate)
        ece = ece / total if total > 0 else 0

        return {
            "curve": curve,
            "ece": round(ece, 4),
            "total_samples": total,
        }

    @classmethod
    def validate_by_play_type(cls, db: Session) -> Dict[str, Any]:
        """
        对5种玩法分别计算准确率（基于已完赛比赛）。
        """
        play_types = {
            "SPF": "胜平负",
            "RQ": "让球胜平负",
            "SCORE": "比分",
            "GOALS": "总进球",
            "HALF": "半全场",
        }
        results = {}

        for pt, label in play_types.items():
            matches = db.query(Match).filter(
                Match.status == MatchStatus.FINISHED,
                Match.actual_outcome.isnot(None),
            ).all()

            total = 0
            correct = 0

            for match in matches:
                pred = db.query(Prediction).filter(
                    Prediction.match_id == match.id,
                    Prediction.play_type == pt,
                ).first()
                if not pred:
                    continue
                total += 1

                probs = pred.probabilities
                pred_out = predicted_outcome(probs)

                if pt == "SPF":
                    actual = match.actual_outcome
                    if pred_out == actual:
                        correct += 1
                elif pt == "RQ":
                    # 让球需要考虑 handicap
                    if match.actual_home_goals is not None and match.actual_away_goals is not None:
                        # Handicap result
                        home_goals = match.actual_home_goals
                        away_goals = match.actual_away_goals
                        # (handicap applied by jingcai_predictor at creation time)
                        actual = "home" if home_goals > away_goals else ("draw" if home_goals == away_goals else "away")
                        if pred_out == actual:
                            correct += 1
                elif pt == "SCORE":
                    actual_score = f"{match.actual_home_goals}:{match.actual_away_goals}"
                    if pred_out == actual_score:
                        correct += 1
                elif pt == "GOALS":
                    actual_goals = (match.actual_home_goals or 0) + (match.actual_away_goals or 0)
                    actual_key = str(actual_goals) if actual_goals < 7 else "7+"
                    if pred_out == actual_key:
                        correct += 1
                elif pt == "HALF":
                    # Half-full requires half-time data which we may not have
                    pass

            results[pt] = {
                "label": label,
                "total": total,
                "correct": correct,
                "accuracy": round(correct / total, 4) if total > 0 else None,
            }

        return results


# ────────────────────────────
# CLI 入口
# ────────────────────────────
if __name__ == "__main__":
    from database.models import init_db, get_db
    
    init_db()
    db = next(get_db())
    
    print("=" * 60)
    print("实时验证报告 — 热身赛/友谊赛")
    print("=" * 60)
    
    report = ValidationEngine.validate_friendly_only(db)
    s = report.summary if hasattr(report, 'summary') else report.to_dict()["summary"]
    
    print(f"\n已结束比赛: {s['finished_matches']} 场")
    print(f"可验证场次: {s['validated_matches']} 场")
    print(f"\n方向准确率: {s['direction_accuracy']:.1%}")
    print(f"  高置信度: {s['high_conf_accuracy']:.1%}")
    print(f"  中置信度: {s['medium_conf_accuracy']:.1%}")
    print(f"  低置信度: {s['low_conf_accuracy']:.1%}")
    print(f"\nBrier Score: {s['avg_brier_score']:.4f} (越低越好)")
    print(f"Log Loss:    {s['avg_log_loss']:.4f}")
    print(f"平均最高概率: {s['avg_max_prob']:.1%}")
    
    print("\n逐场明细:")
    print("-" * 60)
    for m in report.match_results:
        status = "✅" if m.direction_correct else "❌"
        print(f"{status} {m.home_team} vs {m.away_team}")
        print(f"   预测: {m.predicted_outcome} ({m.max_prob:.1%}) | 实际: {m.actual_outcome}")
        print(f"   Brier: {m.brier_score:.4f} | Score: {m.actual_score}")
    
    print("\n✅ 验证完成")
