"""
分层策略 — 三层框架 (high_value / medium_value / skip)

核心设计:
1. 框架固定三层：skip → medium_value → high_value
2. 所有阈值从 StrategyParams 读取，不硬编码数字
3. 以 NN 各子模型输出为判断依据，框架只做分层筛选
4. 5种玩法独立判断、独立出结果
5. 预留自动寻优接口

v2 优化:
- 优化1: 平局独立信号 — NN平局置信达标时独立推荐平局，突破融合公式歧视
- 优化2: 低赔率主胜降权 — 赔率<1.80主胜赢小亏大，降权或跳过
- 优化3: 赔率区间自适应仓位 — 按赔率区间动态调仓位

流程:
输入(单场数据 + 全套NN输出) → 粗筛(skip?) → NN分层(high/medium) → 5种玩法各自出建议
"""
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any

from edge_calculator import EdgeCalculator
from strategy_config import (
    StrategyParams, load_params, DEFAULT_PARAMS,
    compute_position_ratio,
)
from logger import get_logger

logger = get_logger("tiered_strategy")

ec = EdgeCalculator()

# ────────────────────────────
# 场次等级
# ────────────────────────────
TIER_HIGH = "high_value"
TIER_MEDIUM = "medium_value"
TIER_SKIP = "skip"

# 玩法编码
PLAY_SPF = "spf"
PLAY_HANDICAP = "handicap"
PLAY_SCORE = "score"
PLAY_GOALS = "goals"
PLAY_HALFTIME = "halftime"

ALL_PLAYS = [PLAY_SPF, PLAY_HANDICAP, PLAY_SCORE, PLAY_GOALS, PLAY_HALFTIME]

# 中文标签
SEL_LABELS = {"home": "主胜", "draw": "平", "away": "客胜"}
TIER_LABELS = {
    TIER_HIGH: "高价值",
    TIER_MEDIUM: "中等价值",
    TIER_SKIP: "跳过",
}
PLAY_LABELS = {
    PLAY_SPF: "胜平负",
    PLAY_HANDICAP: "让球",
    PLAY_SCORE: "比分",
    PLAY_GOALS: "总进球",
    PLAY_HALFTIME: "半全场",
}


# ────────────────────────────
# 数据结构
# ────────────────────────────

@dataclass(frozen=True)
class PlayRecommendation:
    """单种玩法的预测建议"""
    play_type: str
    play_label: str
    selection: str
    selection_label: str
    confidence: float
    probs: Dict[str, float]
    is_recommended: bool
    position_ratio: float = 0.0   # 优化3: 该玩法推荐的仓位系数
    reason: str = ""


@dataclass(frozen=True)
class MatchTierResult:
    """单场比赛的分层策略结果"""
    match_id: int
    match_code: str = ""

    tier: str = TIER_SKIP
    tier_label: str = "跳过"
    tier_reason: str = ""

    spf_probs: Dict[str, float] = field(default_factory=dict)
    nn_values: Dict[str, float] = field(default_factory=dict)

    edge: float = 0.0
    ev: float = 0.0

    play_recommendations: Dict[str, PlayRecommendation] = field(default_factory=dict)

    position_ratio: float = 0.0
    is_actionable: bool = False
    params_version: str = "tiered_v2"


# ────────────────────────────
# 优化1: 平局独立信号检测
# ────────────────────────────

def check_draw_signal(
    spf_probs: Dict[str, float],
    nn_values: Dict[str, float],
    odds: Dict[str, float],
    params: StrategyParams,
) -> Optional[str]:
    """
    检测平局独立信号。

    数据根因: 综合公式 spf×0.6+nn×0.4 天然歧视平局(5000场仅0.5%推荐平局)，
    但实际平局率23.6%。"推荐非平→实际平"是最大亏损源(-2527单位)。
    修正: 当 NN draw 独立置信达标 + 赔率合理，独立推荐平局。
    """
    nn_draw = nn_values.get("draw", 0.0)
    draw_odds = odds.get("draw", 3.0)
    spf_draw = spf_probs.get("draw", 0.0)

    if nn_draw < params.draw_signal_nn_threshold:
        return None
    if not (params.draw_signal_odds_min <= draw_odds <= params.draw_signal_odds_max):
        return None
    # 赔率死区: 3.0-3.5区间命中率不够覆盖赔率
    if params.draw_signal_odds_deadzone_min <= draw_odds <= params.draw_signal_odds_deadzone_max:
        return None
    if spf_draw < params.draw_signal_min_spf_prob:
        return None

    return f"NN平局信号({nn_draw:.2f})+赔率{draw_odds:.2f}+模型{spf_draw:.2f}"


# ────────────────────────────
# 优化2: SPF 推荐选择（含低赔率主胜降权）
# ────────────────────────────

def select_spf_recommendation(
    spf_probs: Dict[str, float],
    nn_values: Dict[str, float],
    odds: Dict[str, float],
    tier: str,
    params: StrategyParams,
) -> tuple:
    """
    选择胜平负推荐方向，返回 (selection, confidence, reason)。

    优化1: 优先检查平局独立信号
    优化2: 低赔率主胜降权 — 赔率<1.80主胜降权，<1.50跳过
    """
    draw_reason = check_draw_signal(spf_probs, nn_values, odds, params)

    # 综合加权（用于非平局方向的判断）
    combined = {}
    for sel in ("home", "draw", "away"):
        combined[sel] = spf_probs.get(sel, 0.33) * 0.6 + nn_values.get(sel, 0.33) * 0.4

    # ─── 优先级1: 平局独立信号 ───
    if draw_reason:
        draw_conf = nn_values.get("draw", 0.0) * 0.6 + spf_probs.get("draw", 0.0) * 0.4
        return "draw", round(draw_conf, 3), draw_reason

    # ─── 优先级2: 非平局综合推荐 ───
    # 排除平局，取 home/away 中综合更高的
    non_draw = {s: combined[s] for s in ("home", "away") if s in combined}
    if not non_draw:
        best_sel = max(combined, key=combined.get)
        return best_sel, round(combined[best_sel], 3), f"综合推荐{SEL_LABELS.get(best_sel)}"

    best_sel = max(non_draw, key=non_draw.get)
    best_odds = odds.get(best_sel, 2.0)

    # 优化2: 低赔率主胜检查
    if best_sel == "home" and best_odds < params.low_odds_home_skip_threshold:
        # 极低赔率主胜: 检查客胜是否有更高价值
        away_sel = "away"
        away_odds = odds.get("away", 2.0)
        away_conf = non_draw.get("away", 0)
        if away_conf > combined.get(best_sel, 0) * 0.8 and away_odds > params.low_odds_home_damp_threshold:
            return away_sel, round(away_conf, 3), f"主胜赔率过低({best_odds:.2f})，切客胜(赔率{away_odds:.2f})"
        # 无更好选择，标记低赔率
        return best_sel, round(combined[best_sel], 3), f"主胜低赔率({best_odds:.2f})，仓位降权"

    return best_sel, round(combined[best_sel], 3), f"综合推荐{SEL_LABELS.get(best_sel)}"


# ────────────────────────────
# 核心分层逻辑
# ────────────────────────────

def classify_tier(
    spf_probs: Dict[str, float],
    nn_values: Dict[str, float],
    odds: Dict[str, float],
    is_jingcai: bool = False,
    params: Optional[StrategyParams] = None,
) -> tuple:
    """
    判定场次等级 (high / medium / skip)。

    返回: (tier, tier_label, tier_reason, edge_val, ev_val)
    """
    if params is None:
        params = load_params()

    # ─── 第一层：粗筛 (skip) ───
    skip_reason = _coarse_filter(spf_probs, nn_values, odds, params)
    if skip_reason:
        return TIER_SKIP, TIER_LABELS[TIER_SKIP], skip_reason, 0.0, 0.0

    # ─── 边际计算 ───
    edge_result = ec.compute(
        odds.get("home", 2.0), odds.get("draw", 3.0), odds.get("away", 2.0),
        spf_probs, is_jingcai=is_jingcai,
    )

    top_sel = max(spf_probs, key=spf_probs.get)
    sel_edge = edge_result.edges.get(top_sel)
    edge_val = sel_edge.edge if sel_edge else 0.0
    ev_val = sel_edge.ev if sel_edge else 0.0

    # ─── 第二层：high_value ───
    high_reason = _check_high_value(spf_probs, nn_values, odds, edge_val, params)
    if high_reason:
        return TIER_HIGH, TIER_LABELS[TIER_HIGH], high_reason, edge_val, ev_val

    # ─── 第三层：medium_value ───
    medium_reason = _check_medium_value(spf_probs, nn_values, odds, params)
    if medium_reason:
        return TIER_MEDIUM, TIER_LABELS[TIER_MEDIUM], medium_reason, edge_val, ev_val

    return TIER_SKIP, TIER_LABELS[TIER_SKIP], "未达 medium 门槛", edge_val, ev_val


def _coarse_filter(
    spf_probs: Dict[str, float],
    nn_values: Dict[str, float],
    odds: Dict[str, float],
    params: StrategyParams,
) -> str:
    """粗筛：返回空=通过，返回理由=skip"""
    if params.require_odds:
        if not all(odds.get(k) for k in ("home", "draw", "away")):
            return "赔率数据缺失"

    for sel in ("home", "draw", "away"):
        o = odds.get(sel, 0)
        if o < params.odds_min or o > params.odds_max:
            return f"{sel}赔率超出合理范围({o:.2f})"

    if spf_probs:
        top_conf = max(spf_probs.values())
        if top_conf < params.min_top_confidence:
            return f"主模型置信度过低({top_conf:.2f}<{params.min_top_confidence})"

    return ""


def _check_high_value(
    spf_probs: Dict[str, float],
    nn_values: Dict[str, float],
    odds: Dict[str, float],
    edge_val: float,
    params: StrategyParams,
) -> str:
    """判定是否为 high_value"""
    nn_draw = nn_values.get("draw", 0.0)
    draw_odds = odds.get("draw", 3.0)

    # 条件A: NN平局高置信 + 赔率合理区间
    if nn_draw >= params.nn_high_threshold and params.draw_odds_min <= draw_odds <= params.draw_odds_max:
        return f"NN平局高置信({nn_draw:.2f})+赔率合理({draw_odds:.2f})"

    # 条件B: 非 draw 方向，主模型+NN+边际三重确认
    top_sel = max(spf_probs, key=spf_probs.get) if spf_probs else ""
    top_conf = spf_probs.get(top_sel, 0)
    nn_conf = nn_values.get(top_sel, 0)

    if (
        top_sel != "draw"
        and top_conf >= params.high_value_min_model_conf
        and nn_conf >= params.nn_high_threshold
        and edge_val >= params.high_value_min_edge
    ):
        return f"主模型({top_sel}={top_conf:.2f})+NN({nn_conf:.2f})+边际({edge_val:.3f})三重确认"

    return ""


def _check_medium_value(
    spf_probs: Dict[str, float],
    nn_values: Dict[str, float],
    odds: Dict[str, float],
    params: StrategyParams,
) -> str:
    """判定是否为 medium_value"""
    nn_max = max(nn_values.values()) if nn_values else 0
    top_conf = max(spf_probs.values()) if spf_probs else 0
    top_sel = max(spf_probs, key=spf_probs.get) if spf_probs else ""
    nn_pred = max(nn_values, key=nn_values.get) if nn_values else ""
    nn_agrees = nn_pred == top_sel

    # 条件A: NN 达 medium + 主模型达标 + 方向一致
    if nn_max >= params.nn_medium_threshold and top_conf >= params.medium_value_min_model_conf and nn_agrees:
        return f"NN({nn_max:.2f})+主模型({top_conf:.2f})方向一致"

    # 条件B: 主模型极高置信 + NN 未严重分歧
    if top_conf >= params.medium_value_min_model_conf + 0.15 and nn_max >= params.nn_medium_threshold - 0.05:
        return f"主模型高置信({top_conf:.2f})+NN可接受({nn_max:.2f})"

    # 条件C: 平局独立信号（medium 级别）— 优化1 下沉
    draw_reason = check_draw_signal(spf_probs, nn_values, odds, params)
    if draw_reason:
        return draw_reason

    return ""


# ────────────────────────────
# 5种玩法独立建议
# ────────────────────────────

def generate_play_recommendations(
    tier: str,
    spf_probs: Dict[str, float],
    nn_values: Dict[str, float],
    odds: Dict[str, float],
    sub_model_results: Dict[str, Dict],
    params: Optional[StrategyParams] = None,
) -> Dict[str, PlayRecommendation]:
    """按场次等级，5种玩法各自生成独立建议"""
    if params is None:
        params = load_params()

    recommendations: Dict[str, PlayRecommendation] = {}

    if tier == TIER_SKIP:
        for play in ALL_PLAYS:
            recommendations[play] = PlayRecommendation(
                play_type=play, play_label=PLAY_LABELS[play],
                selection="", selection_label="", confidence=0.0,
                probs={}, is_recommended=False, reason="场次等级为skip，跳过",
            )
        return recommendations

    recommendations[PLAY_SPF] = _recommend_spf(spf_probs, nn_values, odds, tier, params)
    recommendations[PLAY_HANDICAP] = _recommend_handicap(sub_model_results.get(PLAY_HANDICAP), tier, params)
    recommendations[PLAY_SCORE] = _recommend_score(sub_model_results.get(PLAY_SCORE), tier, params)
    recommendations[PLAY_GOALS] = _recommend_goals(sub_model_results.get(PLAY_GOALS), tier, params)
    recommendations[PLAY_HALFTIME] = _recommend_halftime(sub_model_results.get(PLAY_HALFTIME), tier, params)

    return recommendations


def _recommend_spf(
    spf_probs: Dict[str, float],
    nn_values: Dict[str, float],
    odds: Dict[str, float],
    tier: str,
    params: StrategyParams,
) -> PlayRecommendation:
    """胜平负玩法建议 — 含优化1(平局信号) + 优化2(低赔率主胜降权)"""
    if not spf_probs:
        return PlayRecommendation(
            play_type=PLAY_SPF, play_label=PLAY_LABELS[PLAY_SPF],
            selection="", selection_label="", confidence=0.0,
            probs={}, is_recommended=False, reason="无SPF概率数据",
        )

    # 优化1+2: 新的选择逻辑
    best_sel, best_conf, reason = select_spf_recommendation(
        spf_probs, nn_values, odds, tier, params,
    )

    best_odds = odds.get(best_sel, 2.0)
    nn_best = max(nn_values.values()) if nn_values else 0

    # 是否推荐
    is_rec = True
    if tier == TIER_HIGH:
        is_rec = best_conf >= params.spf_min_confidence
    elif tier == TIER_MEDIUM:
        is_rec = best_conf >= params.spf_min_confidence and nn_best >= params.nn_medium_threshold

    # 优化2: 极低赔率主胜不推荐
    if best_sel == "home" and best_odds < params.low_odds_home_skip_threshold:
        is_rec = False
        reason += " [赔率过低不推荐]"

    # 优化3: 自适应仓位
    pos = compute_position_ratio(tier, best_sel, best_odds, params)

    return PlayRecommendation(
        play_type=PLAY_SPF,
        play_label=PLAY_LABELS[PLAY_SPF],
        selection=best_sel,
        selection_label=SEL_LABELS.get(best_sel, best_sel),
        confidence=best_conf,
        probs=spf_probs,
        is_recommended=is_rec,
        position_ratio=pos,
        reason=reason,
    )


def _recommend_handicap(
    hc_result: Optional[Dict],
    tier: str,
    params: StrategyParams,
) -> PlayRecommendation:
    """让球玩法建议"""
    if not hc_result or not hc_result.get("ready"):
        return PlayRecommendation(
            play_type=PLAY_HANDICAP, play_label=PLAY_LABELS[PLAY_HANDICAP],
            selection="", selection_label="", confidence=0.0,
            probs={}, is_recommended=False, reason="让球子模型未就绪",
        )

    hc_probs = hc_result.get("handicap_probs", {})
    rec = hc_result.get("recommended", "")
    rec_label = hc_result.get("recommended_label", "")
    conf = hc_result.get("confidence", 0) or (max(hc_probs.values()) if hc_probs else 0)

    is_rec = conf >= params.handicap_min_confidence if tier != TIER_SKIP else False
    reason = f"让球{hc_result.get('handicap', 0)}，推荐{rec_label}" if is_rec else "置信不足"
    pos = params.high_position_ratio if tier == TIER_HIGH else params.medium_position_ratio

    return PlayRecommendation(
        play_type=PLAY_HANDICAP,
        play_label=PLAY_LABELS[PLAY_HANDICAP],
        selection=rec, selection_label=rec_label,
        confidence=round(conf, 3), probs=hc_probs,
        is_recommended=is_rec, position_ratio=pos, reason=reason,
    )


def _recommend_score(
    sc_result: Optional[Dict],
    tier: str,
    params: StrategyParams,
) -> PlayRecommendation:
    """比分玩法建议"""
    if not sc_result or not sc_result.get("ready"):
        return PlayRecommendation(
            play_type=PLAY_SCORE, play_label=PLAY_LABELS[PLAY_SCORE],
            selection="", selection_label="", confidence=0.0,
            probs={}, is_recommended=False, reason="比分子模型未就绪",
        )

    top3 = sc_result.get("top3_scores", [])
    if not top3:
        return PlayRecommendation(
            play_type=PLAY_SCORE, play_label=PLAY_LABELS[PLAY_SCORE],
            selection="", selection_label="", confidence=0.0,
            probs={}, is_recommended=False, reason="无比分预测",
        )

    best = top3[0]
    best_score = best.get("score", "")
    best_prob = best.get("probability", 0)

    is_rec = best_prob >= params.score_min_confidence if tier != TIER_SKIP else False
    reason = f"Top1比分{best_score}(概率{best_prob:.2%})" if is_rec else "比分概率过低"
    probs = {s.get("score", ""): s.get("probability", 0) for s in top3}
    pos = 0.2 if is_rec else 0.0  # 比分天然低概率，轻仓

    return PlayRecommendation(
        play_type=PLAY_SCORE,
        play_label=PLAY_LABELS[PLAY_SCORE],
        selection=best_score, selection_label=best_score,
        confidence=round(best_prob, 3), probs=probs,
        is_recommended=is_rec, position_ratio=pos, reason=reason,
    )


def _recommend_goals(
    goals_result: Optional[Dict],
    tier: str,
    params: StrategyParams,
) -> PlayRecommendation:
    """总进球玩法建议"""
    if not goals_result or not goals_result.get("ready"):
        return PlayRecommendation(
            play_type=PLAY_GOALS, play_label=PLAY_LABELS[PLAY_GOALS],
            selection="", selection_label="", confidence=0.0,
            probs={}, is_recommended=False, reason="总进球子模型未就绪",
        )

    probs = goals_result.get("goals_probs", {})
    rec = goals_result.get("recommended", "")
    rec_label = goals_result.get("recommended_label", "")
    conf = max(probs.values()) if probs else 0

    is_rec = conf >= params.goals_min_confidence if tier != TIER_SKIP else False
    pos = 0.3 if is_rec else 0.0

    return PlayRecommendation(
        play_type=PLAY_GOALS,
        play_label=PLAY_LABELS[PLAY_GOALS],
        selection=rec, selection_label=rec_label,
        confidence=round(conf, 3), probs=probs,
        is_recommended=is_rec, position_ratio=pos,
        reason=f"推荐{rec_label}(概率{conf:.2%})" if is_rec else "概率不足",
    )


def _recommend_halftime(
    ht_result: Optional[Dict],
    tier: str,
    params: StrategyParams,
) -> PlayRecommendation:
    """半全场玩法建议"""
    if not ht_result or not ht_result.get("ready"):
        return PlayRecommendation(
            play_type=PLAY_HALFTIME, play_label=PLAY_LABELS[PLAY_HALFTIME],
            selection="", selection_label="", confidence=0.0,
            probs={}, is_recommended=False, reason="半全场子模型未就绪",
        )

    ht_probs = ht_result.get("halftime_probs", {})
    rec = ht_result.get("recommended", "")
    rec_label = ht_result.get("recommended_label", "")
    conf = max(ht_probs.values()) if ht_probs else 0

    is_rec = conf >= params.halftime_min_confidence if tier != TIER_SKIP else False
    pos = 0.3 if is_rec else 0.0

    return PlayRecommendation(
        play_type=PLAY_HALFTIME,
        play_label=PLAY_LABELS[PLAY_HALFTIME],
        selection=rec, selection_label=rec_label,
        confidence=round(conf, 3), probs=ht_probs,
        is_recommended=is_rec, position_ratio=pos,
        reason=f"推荐{rec_label}(置信{conf:.2f})" if is_rec else "置信不足",
    )


# ────────────────────────────
# 主入口
# ────────────────────────────

def analyze_match(
    match_id: int,
    spf_probs: Dict[str, float],
    nn_values: Dict[str, float],
    odds: Dict[str, float],
    is_jingcai: bool = False,
    sub_model_results: Optional[Dict[str, Dict]] = None,
    params: Optional[StrategyParams] = None,
    match_code: str = "",
) -> MatchTierResult:
    """单场比赛完整分层分析"""
    if params is None:
        params = load_params()
    if sub_model_results is None:
        sub_model_results = {}

    # 分层
    tier, tier_label, tier_reason, edge_val, ev_val = classify_tier(
        spf_probs, nn_values, odds, is_jingcai, params,
    )

    # 5种玩法建议
    play_recs = generate_play_recommendations(tier, spf_probs, nn_values, odds, sub_model_results, params)

    # 自适应仓位：取 SPF 推荐的仓位作为场次的默认仓位
    spf_rec = play_recs.get(PLAY_SPF)
    if spf_rec and spf_rec.is_recommended:
        pos = spf_rec.position_ratio
    else:
        pos = params.high_position_ratio if tier == TIER_HIGH else params.medium_position_ratio

    return MatchTierResult(
        match_id=match_id,
        match_code=match_code,
        tier=tier,
        tier_label=tier_label,
        tier_reason=tier_reason,
        spf_probs=spf_probs,
        nn_values=nn_values,
        edge=round(edge_val, 4),
        ev=round(ev_val, 4),
        play_recommendations=play_recs,
        position_ratio=pos,
        is_actionable=tier in (TIER_HIGH, TIER_MEDIUM),
        params_version=params.version,
    )


# ────────────────────────────
# 从数据库加载并分析
# ────────────────────────────

def analyze_match_from_db(match_id: int, params: Optional[StrategyParams] = None) -> Optional[MatchTierResult]:
    """从数据库加载比赛数据，运行完整分层分析"""
    import json
    from models import SessionLocal, Match, Prediction
    from bet_nn import BetNetPredictor, extract_features

    predictor = BetNetPredictor()
    if not predictor.is_ready():
        return None

    session = SessionLocal()
    try:
        match = session.query(Match).filter(Match.id == match_id).first()
        if not match:
            return None

        pred = session.query(Prediction).filter(
            Prediction.match_id == match_id,
            Prediction.play_type == "SPF",
        ).first()
        if not pred:
            return None

        spf = pred.probabilities if isinstance(pred.probabilities, dict) else json.loads(pred.probabilities)

        score_pred = session.query(Prediction).filter(
            Prediction.match_id == match_id,
            Prediction.play_type == "SCORE",
        ).first()
        score_probs = {}
        if score_pred and score_pred.probabilities:
            score_probs = score_pred.probabilities if isinstance(score_pred.probabilities, dict) else json.loads(score_pred.probabilities)

        odds = {
            "home": match.closing_odds_home or match.odds_home or 2.0,
            "draw": match.closing_odds_draw or match.odds_draw or 3.0,
            "away": match.closing_odds_away or match.odds_away or 2.0,
        }

        odds_movement = {}
        for sel in ("home", "draw", "away"):
            closing = getattr(match, f"closing_odds_{sel}", None) or 0
            opening = getattr(match, f"opening_odds_{sel}", None) or 0
            odds_movement[sel] = (closing - opening) / opening if closing and opening else 0.0

        elo_diff = 0.0
        if match.home_team and match.away_team:
            elo_diff = (match.home_team.elo or 1500) - (match.away_team.elo or 1500)

        raw_feats = extract_features(
            spf_probs=spf, rq_probs=spf, score_top3=score_probs or spf,
            odds=odds, elo_diff=elo_diff, odds_movement=odds_movement,
            competition=match.competition or "",
        )
        nn_values = predictor.predict(raw_feats)

        is_jingcai = match.competition in ("EPL", "Bundesliga", "LaLiga", "SerieA")

        sub_results: Dict[str, Dict] = {}

        try:
            from sub_model_halftime import HalftimePredictor
            ht = HaltimePredictor()
            if ht.is_ready():
                r = ht.predict_from_db(match_id)
                if r and r.get("ready"):
                    sub_results[PLAY_HALFTIME] = r
        except Exception:
            pass

        try:
            from sub_model_score import ScorePredictor
            sc = ScorePredictor()
            if sc.is_ready():
                r = sc.predict_from_db(match_id)
                if r and r.get("ready"):
                    sub_results[PLAY_SCORE] = r
        except Exception:
            pass

        try:
            from sub_model_handicap import HandicapPredictor
            hc = HandicapPredictor()
            if hc.is_ready():
                r = hc.predict_from_db(match_id)
                if r and r.get("ready"):
                    sub_results[PLAY_HANDICAP] = r
        except Exception:
            pass

        return analyze_match(
            match_id=match_id,
            spf_probs=spf,
            nn_values=nn_values,
            odds=odds,
            is_jingcai=is_jingcai,
            sub_model_results=sub_results,
            params=params,
            match_code=match.match_code,
        )

    finally:
        session.close()


# ────────────────────────────
# 结果转字典
# ────────────────────────────

def tier_result_to_dict(result: MatchTierResult) -> Dict[str, Any]:
    """将 MatchTierResult 转为 API 输出字典"""
    plays = {}
    for play_type, rec in result.play_recommendations.items():
        plays[play_type] = {
            "play_label": rec.play_label,
            "selection": rec.selection,
            "selection_label": rec.selection_label,
            "confidence": rec.confidence,
            "is_recommended": rec.is_recommended,
            "position_ratio": rec.position_ratio,
            "reason": rec.reason,
        }

    return {
        "match_id": result.match_id,
        "match_code": result.match_code,
        "tier": result.tier,
        "tier_label": result.tier_label,
        "tier_reason": result.tier_reason,
        "is_actionable": result.is_actionable,
        "position_ratio": result.position_ratio,
        "edge": result.edge,
        "ev": result.ev,
        "spf_probs": result.spf_probs,
        "nn_values": result.nn_values,
        "play_recommendations": plays,
        "params_version": result.params_version,
    }
