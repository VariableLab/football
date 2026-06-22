"""
概率重校准层 — Score/Goals/Half 贝叶斯对齐

基于融合后的 SPF 概率，对衍生玩法进行贝叶斯校准。
"""
from __future__ import annotations

from typing import Dict


def recalibrate_scores(raw_score: Dict[str, float], fused_spf: Dict[str, float], use_heavy_tail: bool = False) -> Dict[str, float]:
    """使用融合 SPF 概率对比分预测进行贝叶斯校准"""
    calibrated = {}
    by_outcome = {"home": {}, "draw": {}, "away": {}}
    for score_key, prob in raw_score.items():
        try:
            parts = score_key.split(':')
            h = int(parts[0].replace('+', ''))
            a = int(parts[1].replace('+', ''))
            outcome = "home" if h > a else ("away" if h < a else "draw")
        except Exception:
            outcome = "draw"
        by_outcome[outcome][score_key] = prob

    sums = {k: sum(v.values()) for k, v in by_outcome.items()}
    for outcome, group in by_outcome.items():
        target_prob = fused_spf.get(outcome, 0.33)
        current_sum = sums[outcome]
        if current_sum > 0:
            for score_key, prob in group.items():
                calibrated[score_key] = (prob / current_sum) * target_prob
        elif target_prob > 0:
            common_scores = {"home": ["1:0", "2:0", "2:1"], "draw": ["1:1", "0:0", "2:2"], "away": ["0:1", "0:2", "1:2"]}
            for cs in common_scores[outcome]:
                calibrated[cs] = target_prob / len(common_scores[outcome])

    total = sum(calibrated.values())
    if total > 0:
        calibrated = {k: round(v / total, 4) for k, v in calibrated.items() if (v / total) >= 0.005}
    return calibrated


def recalibrate_goals(recal_score: Dict[str, float]) -> Dict[str, float]:
    """依据校准后的比分概率，重新生成总进球概率"""
    goals = {str(g): 0.0 for g in range(7)}
    goals["7+"] = 0.0
    for score_key, prob in recal_score.items():
        try:
            parts = score_key.split(':')
            h = int(parts[0].replace('+', ''))
            a = int(parts[1].replace('+', ''))
            total_g = h + a
            if total_g >= 7:
                goals["7+"] += prob
            else:
                goals[str(total_g)] += prob
        except Exception:
            pass
    total = sum(goals.values())
    if total > 0:
        goals = {k: round(v / total, 4) for k, v in goals.items() if (v / total) > 0.002}
    return goals


def recalibrate_half(raw_half: Dict[str, float], fused_spf: Dict[str, float]) -> Dict[str, float]:
    """依据融合后的 SPF 概率，校准半全场转移概率"""
    outcome_map = {
        "主主": "home", "平主": "home", "客主": "home",
        "主平": "draw", "平平": "draw", "客平": "draw",
        "主客": "away", "平客": "away", "客客": "away",
    }
    by_ft = {"home": {}, "draw": {}, "away": {}}
    for key, prob in raw_half.items():
        ft = outcome_map.get(key, "draw")
        by_ft[ft][key] = prob

    sums = {k: sum(v.values()) for k, v in by_ft.items()}
    calibrated = {}
    for ft, group in by_ft.items():
        target_prob = fused_spf.get(ft, 0.33)
        current_sum = sums[ft]
        if current_sum > 0:
            for key, prob in group.items():
                calibrated[key] = (prob / current_sum) * target_prob
        elif target_prob > 0:
            common = [k for k, v in outcome_map.items() if v == ft]
            for c in common:
                calibrated[c] = target_prob / len(common)

    total = sum(calibrated.values())
    if total > 0:
        calibrated = {k: round(v / total, 4) for k, v in calibrated.items()}
    return calibrated
