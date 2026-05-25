"""
综合预测报告 — 整合主模型 + BetNet NN + 分层策略 + 5种子模型

输出:
- 场次等级 (high_value / medium_value / skip)
- 主模型SPF胜平负预测
- BetNet NN预测评分
- 分层策略5种玩法独立参考方向
- 高置信预测标记
"""
import json
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from utils.logger import get_logger

logger = get_logger("prediction_report")


@dataclass
class ComprehensiveReport:
    """综合预测报告"""
    match_id: int
    match_code: str = ""

    # ─── 场次等级 ───
    tier: str = "skip"                # high_value / medium_value / skip
    tier_label: str = "跳过"
    tier_reason: str = ""
    is_actionable: bool = False
    position_ratio: float = 0.0

    # ─── 主模型 ───
    spf_prediction: str = ""  # home / draw / away
    spf_probs: Dict[str, float] = field(default_factory=dict)
    spf_confidence: str = "medium"  # high / medium / low

    # ─── BetNet NN ───
    nn_values: Dict[str, float] = field(default_factory=dict)

    # ─── 边际 ───
    edge: float = 0.0
    ev: float = 0.0

    # ─── 5种玩法参考方向 ───
    play_recommendations: Dict[str, Dict] = field(default_factory=dict)
    # {spf: {selection, selection_label, confidence, is_recommended, reason}, ...}

    # ─── 半场预测(兼容旧接口) ───
    halftime_probs: Dict[str, float] = field(default_factory=dict)
    halftime_prediction: str = ""
    halftime_label: str = ""

    # ─── 比分预测(兼容旧接口) ───
    top3_scores: List[Dict[str, float]] = field(default_factory=list)

    # ─── 让球预测(兼容旧接口) ───
    handicap: int = 0
    handicap_probs: Dict[str, float] = field(default_factory=dict)
    handicap_prediction: str = ""
    handicap_label: str = ""

    # ─── 元信息 ───
    model_version: str = "comprehensive_v2"
    ready: bool = False


SEL_LABELS = {"home": "主胜", "draw": "平", "away": "客胜"}


def generate_report(match_id: int) -> Optional[ComprehensiveReport]:
    """
    生成综合预测报告。

    使用分层策略(tiered_strategy)替代旧的fusion_strategy:
    1. 粗筛 → skip?
    2. NN分层 → high / medium
    3. 5种玩法各自出参考方向
    """
    from database.models import SessionLocal, Match, Prediction

    session = SessionLocal()
    try:
        match = session.query(Match).filter(Match.id == match_id).first()
        if not match:
            return None

        report = ComprehensiveReport(
            match_id=match_id,
            match_code=match.match_code,
        )

        # ─── 1. 主模型SPF ───
        spf_pred = session.query(Prediction).filter(
            Prediction.match_id == match_id,
            Prediction.play_type == "SPF",
        ).first()

        if spf_pred:
            spf = spf_pred.probabilities if isinstance(spf_pred.probabilities, dict) else json.loads(spf_pred.probabilities)
            report.spf_probs = spf
            report.spf_prediction = max(spf, key=spf.get) if spf else ""
            report.spf_confidence = match.confidence or "medium"

        # ─── 2. 分层策略分析 ───
        try:
            from tiered_strategy import analyze_match_from_db
            tier_result = analyze_match_from_db(match_id)
            if tier_result:
                report.tier = tier_result.tier
                report.tier_label = tier_result.tier_label
                report.tier_reason = tier_result.tier_reason
                report.is_actionable = tier_result.is_actionable
                report.position_ratio = tier_result.position_ratio
                report.nn_values = tier_result.nn_values
                report.edge = tier_result.edge
                report.ev = tier_result.ev

                # 5种玩法参考方向
                for play_type, rec in tier_result.play_recommendations.items():
                    report.play_recommendations[play_type] = {
                        "play_label": rec.play_label,
                        "selection": rec.selection,
                        "selection_label": rec.selection_label,
                        "confidence": rec.confidence,
                        "is_recommended": rec.is_recommended,
                        "reason": rec.reason,
                    }
        except Exception as e:
            logger.debug(f"[report] Tiered strategy failed for {match_id}: {e}")

        # ─── 3. 半场预测(兼容旧接口) ───
        try:
            from sub_model_halftime import HalftimePredictor
            ht_pred = HalftimePredictor()
            if ht_pred.is_ready():
                ht_result = ht_pred.predict_from_db(match_id)
                if ht_result and ht_result.get("ready"):
                    report.halftime_probs = ht_result.get("halftime_probs", {})
                    report.halftime_prediction = ht_result.get("recommended", "")
                    report.halftime_label = ht_result.get("recommended_label", "")
        except Exception as e:
            logger.debug(f"[report] Halftime model failed for {match_id}: {e}")

        # ─── 4. 比分预测(兼容旧接口) ───
        try:
            from sub_model_score import ScorePredictor
            sc_pred = ScorePredictor()
            if sc_pred.is_ready():
                sc_result = sc_pred.predict_from_db(match_id)
                if sc_result and sc_result.get("ready"):
                    report.top3_scores = sc_result.get("top3_scores", [])
        except Exception as e:
            logger.debug(f"[report] Score model failed for {match_id}: {e}")

        # ─── 5. 让球预测(兼容旧接口) ───
        try:
            from sub_model_handicap import HandicapPredictor
            hc_pred = HandicapPredictor()
            if hc_pred.is_ready():
                hc_result = hc_pred.predict_from_db(match_id)
                if hc_result and hc_result.get("ready"):
                    report.handicap = hc_result.get("handicap", 0)
                    report.handicap_probs = hc_result.get("handicap_probs", {})
                    report.handicap_prediction = hc_result.get("recommended", "")
                    report.handicap_label = hc_result.get("recommended_label", "")
        except Exception as e:
            logger.debug(f"[report] Handicap model failed for {match_id}: {e}")

        report.ready = True
        return report

    finally:
        session.close()


def report_to_dict(report: ComprehensiveReport) -> Dict:
    """将报告转为API输出字典"""
    return {
        "match_id": report.match_id,
        "match_code": report.match_code,
        "tier": {
            "level": report.tier,
            "label": report.tier_label,
            "reason": report.tier_reason,
            "is_actionable": report.is_actionable,
            "position_ratio": report.position_ratio,
        },
        "spf": {
            "prediction": report.spf_prediction,
            "prediction_label": SEL_LABELS.get(report.spf_prediction, report.spf_prediction),
            "probs": report.spf_probs,
            "confidence": report.spf_confidence,
        },
        "nn_model": {
            "values": report.nn_values,
        },
        "edge": report.edge,
        "ev": report.ev,
        "play_recommendations": report.play_recommendations,
        # 兼容旧接口
        "halftime": {
            "probs": report.halftime_probs,
            "prediction": report.halftime_prediction,
            "label": report.halftime_label,
        },
        "top3_scores": report.top3_scores,
        "handicap": {
            "handicap": report.handicap,
            "probs": report.handicap_probs,
            "prediction": report.handicap_prediction,
            "label": report.handicap_label,
        },
        "model_version": report.model_version,
        "ready": report.ready,
    }
