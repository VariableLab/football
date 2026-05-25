"""
融合策略 — 主引擎 + BetNet NN 互补预测信号

核心规则:
1. NN平局置信度 > 0.6 + 市场平局赔率 2.8-3.5 → 高价值平局信号
2. NN平局置信度 0.5-0.6 + 与融合引擎冲突 → 风险提示(胜负方向不确定)
3. 置信度 < 0.5 的平局信号 → 忽略
4. 市场价值过滤: edge > min_profitable_edge 才标记为高置信预测

不替代融合引擎，而是作为"平局雷达"和"风险过滤器"。
"""
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from edge_calculator import EdgeCalculator
from bet_nn import BetNetPredictor, extract_features
from logger import get_logger

logger = get_logger("fusion_strategy")

ec = EdgeCalculator()


# ────────────────────────────
# 信号类型
# ────────────────────────────
SIGNAL_HIGH_VALUE_DRAW = "strong_draw_signal"
SIGNAL_AVOID = "risk_warning"
SIGNAL_VALUE_BET = "high_confidence_pick"
SIGNAL_NO_SIGNAL = "none"


@dataclass(frozen=True)
class FusionSignal:
    """融合策略产生的预测信号"""
    signal_type: str  # high_value_draw / avoid / value_bet / none
    selection: str  # home / draw / away
    confidence: float  # 0-1
    reason: str  # 中文说明
    nn_draw_value: float  # NN平局置信度
    nn_values: Dict[str, float]  # NN完整输出
    edge: float  # 边际
    ev: float  # 期望值
    odds: float  # 参考赔率


@dataclass(frozen=True)
class FusionResult:
    """融合策略完整结果"""
    # 主引擎预测
    fusion_pred: str  # home / draw / away
    fusion_probs: Dict[str, float]

    # NN输出
    nn_pred: str
    nn_values: Dict[str, float]

    # 信号
    signals: List[FusionSignal]

    # 最终建议
    recommended: str  # 最终推荐选项
    recommended_label: str  # 中文标签
    recommended_confidence: float  # 0-1
    is_high_confidence: bool  # 是否标记为高置信预测
    should_avoid: bool  # 是否建议放弃

    # 元信息
    model_version: str = "fusion_v1"


# ────────────────────────────
# 核心逻辑
# ────────────────────────────
# 赔率区间
DRAW_ODDS_MIN = 2.8
DRAW_ODDS_MAX = 3.5

# 置信度阈值 (BetNet使用Sigmoid输出，值域0.2-0.5，非softmax)
NN_HIGH_THRESHOLD = 0.40
NN_MEDIUM_THRESHOLD = 0.37

# 中文映射
SEL_LABELS = {"home": "主胜", "draw": "平", "away": "客胜"}


def compute_fusion(
    fusion_spf: Dict[str, float],
    nn_values: Dict[str, float],
    odds: Dict[str, float],
    is_jingcai: bool = False,
) -> FusionResult:
    """
    计算融合策略结果。

    Args:
        fusion_spf: 主融合引擎的SPF概率
        nn_values: BetNet NN的3维预测评分
        odds: 收盘赔率 {home, draw, away}
        is_jingcai: 是否竞彩(影响抽水率)
    """
    fusion_pred = max(fusion_spf, key=fusion_spf.get)
    nn_pred = max(nn_values, key=nn_values.get)
    nn_draw = nn_values.get("draw", 0.0)

    signals: List[FusionSignal] = []

    # 边际计算
    edge_result = ec.compute(
        odds.get("home", 2.0),
        odds.get("draw", 3.0),
        odds.get("away", 2.0),
        fusion_spf,
        is_jingcai=is_jingcai,
    )

    draw_edge = edge_result.edges.get("draw")
    draw_ev = draw_edge.ev if draw_edge else 0.0
    draw_edge_val = draw_edge.edge if draw_edge else 0.0
    draw_odds = odds.get("draw", 3.0)

    # ─── 规则1: 高价值平局信号 ───
    if nn_draw > NN_HIGH_THRESHOLD and DRAW_ODDS_MIN <= draw_odds <= DRAW_ODDS_MAX:
        signals.append(FusionSignal(
            signal_type=SIGNAL_HIGH_VALUE_DRAW,
            selection="draw",
            confidence=nn_draw,
            reason=f"NN平局高置信度({nn_draw:.2f})+赔率合理({draw_odds:.2f})",
            nn_draw_value=nn_draw,
            nn_values=nn_values,
            edge=draw_edge_val,
            ev=draw_ev,
            odds=draw_odds,
        ))

    # ─── 规则2: 风险提示 ───
    if NN_MEDIUM_THRESHOLD < nn_draw <= NN_HIGH_THRESHOLD and fusion_pred != "draw":
        signals.append(FusionSignal(
            signal_type=SIGNAL_AVOID,
            selection=fusion_pred,
            confidence=nn_draw,
            reason=f"NN平局中置信度({nn_draw:.2f})与融合引擎({fusion_pred})冲突，建议回避",
            nn_draw_value=nn_draw,
            nn_values=nn_values,
            edge=0.0,
            ev=0.0,
            odds=odds.get(fusion_pred, 2.0),
        ))

    # ─── 规则3: 价值预测信号(非平局) ───
    for sel in ["home", "away"]:
        sel_edge = edge_result.edges.get(sel)
        if sel_edge and sel_edge.is_value and nn_values.get(sel, 0) > NN_MEDIUM_THRESHOLD:
            signals.append(FusionSignal(
                signal_type=SIGNAL_VALUE_BET,
                selection=sel,
                confidence=nn_values.get(sel, 0),
                reason=f"NN({sel})={nn_values.get(sel, 0):.2f}+边际={sel_edge.edge:.3f}+EV={sel_edge.ev:.3f}",
                nn_draw_value=nn_draw,
                nn_values=nn_values,
                edge=sel_edge.edge,
                ev=sel_edge.ev,
                odds=sel_edge.odds,
            ))

    # ─── 决定最终推荐 ───
    should_avoid = any(s.signal_type == SIGNAL_AVOID for s in signals)
    high_draw = next(
        (s for s in signals if s.signal_type == SIGNAL_HIGH_VALUE_DRAW), None
    )

    if high_draw:
        recommended = "draw"
        recommended_confidence = high_draw.confidence
        is_value = True
    elif should_avoid:
        # 风险提示时保留融合方向但标记should_avoid(建议谨慎参考)
        recommended = fusion_pred
        recommended_confidence = fusion_spf.get(fusion_pred, 0.4)
        is_value = False
    else:
        recommended = fusion_pred
        recommended_confidence = fusion_spf.get(fusion_pred, 0.4)
        value_signal = next(
            (s for s in signals if s.signal_type == SIGNAL_VALUE_BET and s.selection == fusion_pred),
            None,
        )
        is_value = value_signal is not None

    return FusionResult(
        fusion_pred=fusion_pred,
        fusion_probs=fusion_spf,
        nn_pred=nn_pred,
        nn_values=nn_values,
        signals=signals,
        recommended=recommended,
        recommended_label=SEL_LABELS.get(recommended, recommended),
        recommended_confidence=round(recommended_confidence, 3),
        is_high_confidence=is_value,
        should_avoid=should_avoid,
    )


def compute_fusion_from_db(match_id: int) -> Optional[FusionResult]:
    """从数据库加载比赛数据，运行融合策略"""
    import json
    from models import SessionLocal, Match, MatchStatus, Prediction

    predictor = BetNetPredictor()
    if not predictor.is_ready():
        return None

    session = SessionLocal()
    try:
        match = session.query(Match).filter(Match.id == match_id).first()
        if not match:
            return None

        # 获取SPF预测
        pred = session.query(Prediction).filter(
            Prediction.match_id == match_id,
            Prediction.play_type == "SPF",
        ).first()
        if not pred:
            return None

        spf = pred.probabilities if isinstance(pred.probabilities, dict) else json.loads(pred.probabilities)

        # 获取比分预测
        score_pred = session.query(Prediction).filter(
            Prediction.match_id == match_id,
            Prediction.play_type == "SCORE",
        ).first()
        score_probs = {}
        if score_pred and score_pred.probabilities:
            score_probs = score_pred.probabilities if isinstance(score_pred.probabilities, dict) else json.loads(score_pred.probabilities)

        # 赔率
        odds = {
            "home": match.closing_odds_home or match.odds_home or 2.0,
            "draw": match.closing_odds_draw or match.odds_draw or 3.0,
            "away": match.closing_odds_away or match.odds_away or 2.0,
        }

        # 赔率变动
        odds_movement = {}
        for sel in ["home", "draw", "away"]:
            closing = getattr(match, f"closing_odds_{sel}", None) or 0
            opening = getattr(match, f"opening_odds_{sel}", None) or 0
            odds_movement[sel] = (closing - opening) / opening if closing and opening else 0.0

        # Elo差
        elo_diff = 0.0
        if match.home_team and match.away_team:
            h_elo = match.home_team.elo or 1500
            a_elo = match.away_team.elo or 1500
            elo_diff = h_elo - a_elo

        # NN推理
        raw_feats = extract_features(
            spf_probs=spf, rq_probs=spf, score_top3=score_probs or spf,
            odds=odds, elo_diff=elo_diff, odds_movement=odds_movement,
            competition=match.competition or "",
        )
        nn_values = predictor.predict(raw_feats)

        # 判断竞彩
        is_jingcai = match.competition in ("EPL", "Bundesliga", "LaLiga", "SerieA")

        return compute_fusion(
            fusion_spf=spf,
            nn_values=nn_values,
            odds=odds,
            is_jingcai=is_jingcai,
        )
    finally:
        session.close()
