"""
策略闭环监控 — NN 重训练后参数适配 + 异常检测 + 迭代规划

闭环链路:
  NN 重训练 → 参数寻优(优先级排序) → 回测验证 → 自动/人工上线
               ↑                                     |
               └── 异常检测(命中率/赔率漂移) ←─────────┘

三大功能:
1. NN 重训练后参数优先级: 哪些阈值该先调、为什么
2. 异常监控规则: medium 层命中率/赔率漂移自动触发寻优
3. 迭代计划: 接下来 3 轮寻优分别重点优化什么
"""
import json
import math
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Tuple

from strategy_config import StrategyParams, load_params, save_params, update_params
from logger import get_logger
from alert_manager import fire_alert

logger = get_logger("strategy_monitor")

MONITOR_DIR = "./data/strategy/monitor"
os.makedirs(MONITOR_DIR, exist_ok=True)

SNAPSHOT_PATH = os.path.join(MONITOR_DIR, "baseline_snapshots.json")
DRIFT_LOG_PATH = os.path.join(MONITOR_DIR, "drift_log.json")


# ────────────────────────────
# 一、NN 重训练后参数优先级
# ────────────────────────────

@dataclass(frozen=True)
class ParamPriority:
    """单个参数的优先级描述"""
    name: str
    priority: int  # 1=最高, 5=最低
    reason: str
    search_range: Tuple[float, float]
    step: float
    sensitivity: str  # high/medium/low — 对结果的影响程度


# NN 重训练后，参数寻优优先级排序
# 逻辑: NN 输出分布变化直接影响哪些阈值 → 这些阈值优先调整
NN_RETRAIN_PRIORITIES: Tuple[ParamPriority, ...] = (
    # ── 第一优先级: NN 置信门槛 (NN 输出分布直接决定) ──
    ParamPriority(
        name="nn_high_threshold",
        priority=1,
        reason="NN 重训练后 sigmoid 输出分布可能偏移，0.45 门槛可能不再对应 '强信号'。"
               "例: 新模型整体输出偏低 → 原 0.45 可能筛空 high 层",
        search_range=(0.35, 0.55),
        step=0.02,
        sensitivity="high",
    ),
    ParamPriority(
        name="nn_medium_threshold",
        priority=1,
        reason="medium 层入场门槛，直接决定 90%+ 场次的分层结果。"
               "NN 输出分布偏移 → 原 0.40 门槛对应的有效命中率可能剧变",
        search_range=(0.30, 0.48),
        step=0.02,
        sensitivity="high",
    ),

    # ── 第二优先级: 平局信号参数 (draw NN 子模型输出变化) ──
    ParamPriority(
        name="draw_signal_nn_threshold",
        priority=2,
        reason="平局 NN 置信门槛 — 如果 draw 子模型重训练后校准偏移，"
               "0.40 门槛可能过于宽松或严格，直接影响平局推荐量",
        search_range=(0.30, 0.50),
        step=0.02,
        sensitivity="high",
    ),
    ParamPriority(
        name="draw_signal_odds_deadzone_min",
        priority=2,
        reason="平局赔率死区下界 — NN 对平局判断力变化后，"
               "原来 3.0-3.5 的死区可能不再准确",
        search_range=(2.80, 3.30),
        step=0.05,
        sensitivity="medium",
    ),
    ParamPriority(
        name="draw_signal_odds_deadzone_max",
        priority=2,
        reason="死区上界 — 同上，需要跟下界联动调整",
        search_range=(3.30, 4.00),
        step=0.05,
        sensitivity="medium",
    ),

    # ── 第三优先级: 边际/模型置信门槛 (间接受 NN 影响) ──
    ParamPriority(
        name="high_value_min_edge",
        priority=3,
        reason="边际门槛 — NN 输出通过 edge_calculator 间接影响边际值，"
               "NN 变化 → 概率变化 → 边际变化，但不是直接映射",
        search_range=(0.01, 0.05),
        step=0.005,
        sensitivity="medium",
    ),
    ParamPriority(
        name="high_value_min_model_conf",
        priority=3,
        reason="主模型置信门槛 — 间接受 NN 影响(综合公式含 NN 权重 0.4)",
        search_range=(0.35, 0.50),
        step=0.02,
        sensitivity="medium",
    ),
    ParamPriority(
        name="medium_value_min_model_conf",
        priority=3,
        reason="medium 主模型门槛 — 与 nn_medium_threshold 联动",
        search_range=(0.30, 0.42),
        step=0.02,
        sensitivity="medium",
    ),

    # ── 第四优先级: 赔率区间参数 (NN 变化不直接影响) ──
    ParamPriority(
        name="low_odds_home_skip_threshold",
        priority=4,
        reason="低赔率主胜跳过门槛 — 与赔率市场结构相关，NN 变化不直接影响，"
               "但 NN 变化可能改变主胜推荐频率",
        search_range=(1.30, 1.70),
        step=0.05,
        sensitivity="low",
    ),
    ParamPriority(
        name="low_odds_home_damp_threshold",
        priority=4,
        reason="低赔率主胜降权门槛 — 同上",
        search_range=(1.60, 2.00),
        step=0.05,
        sensitivity="low",
    ),
    ParamPriority(
        name="draw_odds_min",
        priority=4,
        reason="high 层平局赔率区间下界 — 市场结构参数，不随 NN 变化",
        search_range=(2.60, 3.30),
        step=0.05,
        sensitivity="low",
    ),
    ParamPriority(
        name="draw_odds_max",
        priority=4,
        reason="high 层平局赔率区间上界 — 同上",
        search_range=(3.10, 3.80),
        step=0.05,
        sensitivity="low",
    ),

    # ── 第五优先级: 仓位/抽水等 (NN 变化几乎不影响) ──
    ParamPriority(
        name="min_top_confidence",
        priority=5,
        reason="粗筛最低置信度 — 极少需要调整，NN 变化不直接影响",
        search_range=(0.20, 0.40),
        step=0.05,
        sensitivity="low",
    ),
)


def generate_retrain_search_space() -> Dict:
    """
    NN 重训练后，按优先级生成参数搜索空间。

    策略:
    - 第1轮(优先级1-2): 只搜 NN 直接相关的 5 个参数
    - 第2轮(优先级1-3): 扩展到间接相关的 8 个参数
    - 第3轮(优先级1-5): 全参数搜索
    """
    rounds = {
        "round1_nn_core": {
            "description": "NN 核心参数: 置信门槛 + 平局信号",
            "params": {},
        },
        "round2_extended": {
            "description": "扩展参数: + 边际/模型置信",
            "params": {},
        },
        "round3_full": {
            "description": "全参数搜索",
            "params": {},
        },
    }

    for pp in NN_RETRAIN_PRIORITIES:
        lo, hi, step = pp.search_range[0], pp.search_range[1], pp.step
        values = []
        v = lo
        while v <= hi + 1e-9:
            values.append(round(v, 4))
            v += step

        entry = {pp.name: values, "_priority": pp.priority, "_reason": pp.reason}

        if pp.priority <= 2:
            rounds["round1_nn_core"]["params"][pp.name] = values
        if pp.priority <= 3:
            rounds["round2_extended"]["params"][pp.name] = values
        rounds["round3_full"]["params"][pp.name] = values

    return rounds


def save_search_space(round_key: str, search_space: Dict) -> str:
    """保存搜索空间到文件，供 param_optimizer 使用"""
    path = os.path.join(MONITOR_DIR, f"search_space_{round_key}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(search_space, f, indent=2, ensure_ascii=False)
    return path


# ────────────────────────────
# 二、异常监控规则
# ────────────────────────────

@dataclass(frozen=True)
class DriftRule:
    """单条漂移检测规则"""
    name: str
    metric: str  # 要检测的指标
    window_days: int  # 统计窗口(天)
    threshold: float  # 触发阈值
    comparison: str  # "below" / "above" / "delta_above"
    severity: str  # "warning" / "critical"
    action: str  # 触发后的动作描述
    cooldown_hours: int = 24  # 同规则冷却时间


# 监控规则: medium 层命中率 + 赔率分布漂移
DRIFT_RULES: Tuple[DriftRule, ...] = (
    # ── 命中率漂移 ──
    DriftRule(
        name="medium_hit_rate_drop",
        metric="medium_hit_rate",
        window_days=30,
        threshold=0.50,
        comparison="below",
        severity="critical",
        action="trigger_param_optimize",
        cooldown_hours=48,
    ),
    DriftRule(
        name="medium_hit_rate_vs_baseline",
        metric="medium_hit_rate_delta",
        window_days=30,
        threshold=0.05,
        comparison="delta_above",
        severity="warning",
        action="alert_and_snapshot",
        cooldown_hours=24,
    ),
    DriftRule(
        name="high_hit_rate_drop",
        metric="high_hit_rate",
        window_days=30,
        threshold=0.60,
        comparison="below",
        severity="critical",
        action="trigger_param_optimize",
        cooldown_hours=48,
    ),

    # ── ROI 漂移 ──
    DriftRule(
        name="medium_roi_negative_deep",
        metric="medium_roi",
        window_days=30,
        threshold=-0.02,
        comparison="below",
        severity="critical",
        action="trigger_param_optimize",
        cooldown_hours=48,
    ),
    DriftRule(
        name="combined_roi_negative",
        metric="combined_roi",
        window_days=30,
        threshold=0.0,
        comparison="below",
        severity="warning",
        action="alert_and_snapshot",
        cooldown_hours=24,
    ),

    # ── 赔率分布漂移 ──
    DriftRule(
        name="low_odds_home_surge",
        metric="low_odds_home_ratio",
        window_days=30,
        threshold=0.40,
        comparison="above",
        severity="warning",
        action="review_low_odds_thresholds",
        cooldown_hours=72,
    ),
    DriftRule(
        name="high_odds_bucket_roi_collapse",
        metric="high_odds_bucket_roi",
        window_days=30,
        threshold=-0.05,
        comparison="below",
        severity="critical",
        action="trigger_param_optimize",
        cooldown_hours=48,
    ),
    DriftRule(
        name="draw_rate_shift",
        metric="actual_draw_rate",
        window_days=60,
        threshold=0.05,
        comparison="delta_above",
        severity="warning",
        action="review_draw_signal_params",
        cooldown_hours=72,
    ),

    # ── 样本量/覆盖率 ──
    DriftRule(
        name="medium_coverage_collapse",
        metric="medium_coverage_rate",
        window_days=30,
        threshold=0.10,
        comparison="below",
        severity="warning",
        action="alert_and_snapshot",
        cooldown_hours=72,
    ),
)


@dataclass(frozen=True)
class DriftSnapshot:
    """某个时间窗口的策略表现快照"""
    period_start: str
    period_end: str
    sample_size: int
    metrics: Dict[str, float]
    triggered_rules: Tuple[str, ...] = ()


def _compute_window_metrics(days: int) -> Optional[DriftSnapshot]:
    """计算最近 N 天的策略表现指标"""
    from models import SessionLocal, Match, MatchStatus, Prediction

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=days)

    session = SessionLocal()
    try:
        matches = session.query(Match).filter(
            Match.status == MatchStatus.FINISHED,
            Match.actual_outcome.isnot(None),
            Match.closing_odds_home.isnot(None),
            Match.closing_odds_home > 1.01,
            Match.kickoff_at >= cutoff,
        ).all()

        if len(matches) < 20:
            return None

        from tiered_strategy import classify_tier, select_spf_recommendation
        from strategy_config import compute_position_ratio
        # from bet_nn import BetNetPredictor, extract_features

        predictor = BetNetPredictor()
        if not predictor.is_ready():
            return None

        params = load_params()

        match_ids = [m.id for m in matches]
        preds = session.query(Prediction).filter(
            Prediction.match_id.in_(match_ids),
            Prediction.play_type == "SPF",
        ).all()
        pred_map = {}
        for p in preds:
            probs = p.probabilities if isinstance(p.probabilities, dict) else json.loads(p.probabilities)
            pred_map[p.match_id] = probs

        # 逐场统计
        high_count = 0
        high_correct = 0
        high_profit = 0.0
        high_stakes = 0.0
        medium_count = 0
        medium_correct = 0
        medium_profit = 0.0
        medium_stakes = 0.0
        skip_count = 0
        low_odds_home_count = 0
        high_odds_count = 0
        high_odds_profit = 0.0
        high_odds_stakes = 0.0
        draw_actual = 0

        for match in matches:
            spf = pred_map.get(match.id)
            if not spf:
                continue

            odds = {
                "home": match.closing_odds_home,
                "draw": match.closing_odds_draw,
                "away": match.closing_odds_away,
            }

            odds_movement = {}
            for sel in ("home", "draw", "away"):
                closing = getattr(match, f"closing_odds_{sel}", None) or 0
                opening = getattr(match, f"opening_odds_{sel}", None) or 0
                odds_movement[sel] = (closing - opening) / opening if closing and opening else 0.0

            elo_diff = 0.0
            if match.home_team and match.away_team:
                elo_diff = (match.home_team.elo or 1500) - (match.away_team.elo or 1500)

            score_pred = session.query(Prediction).filter(
                Prediction.match_id == match.id,
                Prediction.play_type == "SCORE",
            ).first()
            score_probs = {}
            if score_pred and score_pred.probabilities:
                score_probs = (
                    score_pred.probabilities
                    if isinstance(score_pred.probabilities, dict)
                    else json.loads(score_pred.probabilities)
                )

            raw_feats = extract_features(
                spf_probs=spf, rq_probs=spf, score_top3=score_probs or spf,
                odds=odds, elo_diff=elo_diff, odds_movement=odds_movement,
                competition=match.competition or "",
            )
            nn_values = predictor.predict(raw_feats)
            is_jingcai = match.competition in ("EPL", "Bundesliga", "LaLiga", "SerieA")

            tier, _, _, _, _ = classify_tier(spf, nn_values, odds, is_jingcai, params)
            actual = match.actual_outcome

            if actual == "draw":
                draw_actual += 1

            if tier == TIER_SKIP:
                skip_count += 1
                continue

            predicted, _, _ = select_spf_recommendation(spf, nn_values, odds, tier, params)
            pred_odds = odds.get(predicted, 2.0)
            pos = compute_position_ratio(tier, predicted, pred_odds, params)
            if pos <= 0:
                skip_count += 1
                continue

            is_correct = predicted == actual
            profit = (pred_odds - 1.0) * pos if is_correct else -pos

            # 低赔率主胜占比
            if predicted == "home" and pred_odds < 1.80:
                low_odds_home_count += 1

            # 高赔率区间(5.00+)统计
            if pred_odds >= 5.00:
                high_odds_count += 1
                high_odds_stakes += pos
                high_odds_profit += profit if is_correct else -pos

            if tier == TIER_HIGH:
                high_count += 1
                high_stakes += pos
                if is_correct:
                    high_correct += 1
                    high_profit += (pred_odds - 1.0) * pos
                else:
                    high_profit -= pos
            else:
                medium_count += 1
                medium_stakes += pos
                if is_correct:
                    medium_correct += 1
                    medium_profit += (pred_odds - 1.0) * pos
                else:
                    medium_profit -= pos

        total_active = high_count + medium_count
        total_all = total_active + skip_count

        metrics = {
            "medium_hit_rate": medium_correct / medium_count if medium_count > 0 else 0.0,
            "medium_roi": medium_profit / medium_stakes if medium_stakes > 0 else 0.0,
            "high_hit_rate": high_correct / high_count if high_count > 0 else 0.0,
            "high_roi": high_profit / high_stakes if high_stakes > 0 else 0.0,
            "combined_roi": (high_profit + medium_profit) / (high_stakes + medium_stakes)
                if (high_stakes + medium_stakes) > 0 else 0.0,
            "low_odds_home_ratio": low_odds_home_count / total_active if total_active > 0 else 0.0,
            "high_odds_bucket_roi": high_odds_profit / high_odds_stakes if high_odds_stakes > 0 else 0.0,
            "actual_draw_rate": draw_actual / total_all if total_all > 0 else 0.0,
            "medium_coverage_rate": medium_count / total_all if total_all > 0 else 0.0,
        }

        return DriftSnapshot(
            period_start=cutoff.isoformat(),
            period_end=now.isoformat(),
            sample_size=total_all,
            metrics=metrics,
        )

    finally:
        session.close()


def load_baseline_snapshot() -> Optional[Dict]:
    """加载基线快照（最近一次参数寻优时的表现）"""
    if not os.path.exists(SNAPSHOT_PATH):
        return None
    try:
        with open(SNAPSHOT_PATH, "r", encoding="utf-8") as f:
            snapshots = json.load(f)
        return snapshots[-1] if snapshots else None
    except (json.JSONDecodeError, IOError):
        return None


def save_baseline_snapshot(metrics: Dict[str, float], label: str = "") -> None:
    """保存基线快照"""
    snapshots = []
    if os.path.exists(SNAPSHOT_PATH):
        try:
            with open(SNAPSHOT_PATH, "r", encoding="utf-8") as f:
                snapshots = json.load(f)
        except (json.JSONDecodeError, IOError):
            pass

    snapshots.append({
        "label": label or f"snapshot_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M')}",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "metrics": metrics,
    })

    # 只保留最近 10 个快照
    snapshots = snapshots[-10:]

    with open(SNAPSHOT_PATH, "w", encoding="utf-8") as f:
        json.dump(snapshots, f, indent=2, ensure_ascii=False)


def check_drift() -> List[Dict]:
    """
    执行所有漂移检测规则，返回触发的告警列表。

    每条规则:
    1. 计算最近 window_days 天的指标
    2. 与阈值/基线对比
    3. 触发则记录 + 告警 + 可选触发寻优
    """
    # 加载漂移日志(用于冷却)
    drift_log = _load_drift_log()

    # 按所需窗口分组计算(避免重复计算)
    window_cache: Dict[int, Optional[DriftSnapshot]] = {}
    baseline = load_baseline_snapshot()

    triggered = []

    for rule in DRIFT_RULES:
        # 冷却检查
        last_fire = drift_log.get(rule.name, {}).get("last_fire_ts", 0)
        if last_fire > 0:
            hours_since = (datetime.now(timezone.utc).timestamp() - last_fire) / 3600
            if hours_since < rule.cooldown_hours:
                continue

        # 获取窗口指标
        if rule.window_days not in window_cache:
            window_cache[rule.window_days] = _compute_window_metrics(rule.window_days)

        snapshot = window_cache.get(rule.window_days)
        if not snapshot or snapshot.sample_size < 20:
            continue

        val = snapshot.metrics.get(rule.metric, 0.0)
        is_triggered = False

        if rule.comparison == "below" and val < rule.threshold:
            is_triggered = True
        elif rule.comparison == "above" and val > rule.threshold:
            is_triggered = True
        elif rule.comparison == "delta_above":
            # 与基线对比的偏移量
            if baseline:
                baseline_val = baseline.get("metrics", {}).get(rule.metric, 0.0)
                delta = abs(val - baseline_val)
                if delta > rule.threshold:
                    is_triggered = True

        if is_triggered:
            alert_msg = (
                f"[策略漂移] {rule.name}: "
                f"{rule.metric}={val:.4f}, "
                f"阈值={rule.threshold}, "
                f"窗口={rule.window_days}天, "
                f"样本={snapshot.sample_size}"
            )

            fire_alert(
                source="strategy_monitor",
                level=rule.severity,
                message=alert_msg,
            )

            triggered.append({
                "rule": rule.name,
                "metric": rule.metric,
                "value": round(val, 4),
                "threshold": rule.threshold,
                "severity": rule.severity,
                "action": rule.action,
                "window_days": rule.window_days,
                "sample_size": snapshot.sample_size,
            })

            # 更新漂移日志
            drift_log[rule.name] = {
                "last_fire_ts": datetime.now(timezone.utc).timestamp(),
                "last_fire_time": datetime.now(timezone.utc).isoformat(),
                "value": round(val, 4),
                "threshold": rule.threshold,
            }

    # 保存漂移日志
    _save_drift_log(drift_log)

    if triggered:
        logger.warning(f"[strategy_monitor] 触发 {len(triggered)} 条漂移规则")
    else:
        logger.info("[strategy_monitor] 所有漂移规则正常")

    return triggered


def _load_drift_log() -> Dict:
    if not os.path.exists(DRIFT_LOG_PATH):
        return {}
    try:
        with open(DRIFT_LOG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return {}


def _save_drift_log(log: Dict) -> None:
    with open(DRIFT_LOG_PATH, "w", encoding="utf-8") as f:
        json.dump(log, f, indent=2, ensure_ascii=False)


def should_trigger_optimize(triggered: List[Dict]) -> bool:
    """判断是否需要立即触发参数寻优（而非等双周周期）"""
    critical_rules = [t for t in triggered if t["severity"] == "critical"]
    if len(critical_rules) >= 2:
        return True
    if any(t["action"] == "trigger_param_optimize" for t in triggered):
        return True
    return False


# ────────────────────────────
# 三、迭代计划 (3 轮)
# ────────────────────────────

@dataclass(frozen=True)
class IterationPlan:
    """单轮迭代计划"""
    round_num: int
    title: str
    focus_params: Tuple[str, ...]
    goal: str
    search_strategy: str
    expected_outcome: str
    success_criteria: str


ITERATION_PLANS: Tuple[IterationPlan, ...] = (

    # ── 第 1 轮: NN 门槛校准 (紧接 NN 重训练后) ──
    IterationPlan(
        round_num=1,
        title="NN 门槛校准 + medium 层止损",
        focus_params=(
            "nn_high_threshold",
            "nn_medium_threshold",
            "draw_signal_nn_threshold",
            "draw_signal_odds_deadzone_min",
            "draw_signal_odds_deadzone_max",
        ),
        goal=(
            "校准新 NN 模型的置信门槛，使 high 层命中率稳定在 70%+，"
            "medium 层命中率恢复到 55%+（或 ROI 回到 -1% 以内）。"
            "平局信号门槛适配新 draw 子模型。"
        ),
        search_strategy=(
            "只搜优先级 1-2 的 5 个参数（NN 核心参数）。"
            "组合数: 5 参数 × 5 值 ≈ 3125 组，采样 500 组。"
            "预计耗时: ~30 分钟（19K 样本 × 500 组）。"
        ),
        expected_outcome=(
            "high 层: 命中率 70%+，ROI 3%+"
            "medium 层: ROI 从 -0.82% 改善到 -0.5% 以内"
            "平局推荐: 命中率维持在 30%+ (赔率3.5-4.0区间)"
        ),
        success_criteria=(
            "high 层命中率 ≥ 70% (不低于重训练前)"
            "medium 层 ROI ≥ -0.5% (改善 0.3pp+)"
            "high 层场次 ≥ 100 (不被筛空)"
        ),
    ),

    # ── 第 2 轮: medium 层精细化 (第 1 轮结果稳定后) ──
    IterationPlan(
        round_num=2,
        title="medium 层精细化 + 仓位优化",
        focus_params=(
            "nn_medium_threshold",
            "medium_value_min_model_conf",
            "high_value_min_edge",
            "high_value_min_model_conf",
            "low_odds_home_skip_threshold",
            "low_odds_home_damp_threshold",
            "draw_odds_min",
            "draw_odds_max",
        ),
        goal=(
            "在 NN 门槛已校准的基础上，精细化 medium 层的入场条件。"
            "重点: 低赔率主胜的降权区间、high 层边际门槛、赔率过滤区间。"
            "目标: medium 层 ROI 接近 0 或微正。"
        ),
        search_strategy=(
            "扩展到优先级 1-3 的 8 个参数。"
            "第 1 轮已确定的 NN 门槛可作为锚点，缩小搜索范围。"
            "组合数: 8 参数，每参数 3-4 值 ≈ 6000 组，采样 500 组。"
            "加入仓位映射搜索: odds_position_map 的 6 个仓位系数各搜 3 档。"
        ),
        expected_outcome=(
            "medium 层: ROI 从 -0.5% 改善到 0% 附近"
            "high 层: ROI 不低于 3%（不被稀释）"
            "1.50-1.80 赔率区间: ROI 从 -3% 改善到 -1% 以内"
        ),
        success_criteria=(
            "medium 层 ROI ≥ -0.2% (接近打平)"
            "high 层 ROI ≥ 3% (不受影响)"
            "combined ROI ≥ 0% (整体打平)"
            "medium 层样本 ≥ 3000 (不过度筛选)"
        ),
    ),

    # ── 第 3 轮: 组合优化 + 仓位体系 (前 2 轮结果稳定后) ──
    IterationPlan(
        round_num=3,
        title="组合方案验证 + 自适应仓位体系",
        focus_params=(
            "nn_high_threshold",
            "nn_medium_threshold",
            "draw_signal_nn_threshold",
            "draw_signal_odds_deadzone_min",
            "draw_signal_odds_deadzone_max",
            "low_odds_home_skip_threshold",
            "low_odds_home_damp_threshold",
            "odds_position_map",
            "high_position_ratio",
            "medium_position_ratio",
        ),
        goal=(
            "全参数联合搜索，验证前两轮的局部最优是否为全局最优。"
            "重点优化仓位体系: odds_position_map 的 6 个区间系数。"
            "目标: medium 层 ROI 转正，combined ROI 达到 1%+。"
        ),
        search_strategy=(
            "前 2 轮的局部最优参数作为起始点，全参数联合搜索。"
            "仓位映射: 6 个区间 × 4 档仓位(0/0.2/0.5/0.8) = 4096 种，采样 200 种。"
            "其他参数: 每参数 3 值(锚点±1步)。"
            "总组合: ~200(仓位) × 8(其他参数采样) ≈ 1600 组，采样 500 组。"
        ),
        expected_outcome=(
            "medium 层: ROI ≥ 0% (转正)"
            "high 层: ROI ≥ 3% (不退化)"
            "combined ROI ≥ 1% (稳定盈利)"
            "最大回撤: 不超过基线的 1.3 倍"
        ),
        success_criteria=(
            "medium ROI ≥ 0% (核心目标)"
            "combined ROI ≥ 0.5% (整体盈利)"
            "high ROI ≥ 2.5% (可接受小幅波动)"
            "coverage_rate ≥ 10% (不过度筛选)"
            "max_drawdown ≤ 基线 × 1.3"
        ),
    ),
)


# ────────────────────────────
# 调度入口
# ────────────────────────────

def strategy_monitor_job() -> None:
    """调度器定时任务：策略漂移检测"""
    triggered = check_drift()

    if triggered:
        # 保存触发记录
        record_path = os.path.join(MONITOR_DIR, "latest_trigger.json")
        with open(record_path, "w", encoding="utf-8") as f:
            json.dump({
                "checked_at": datetime.now(timezone.utc).isoformat(),
                "triggered_count": len(triggered),
                "triggered": triggered,
            }, f, indent=2, ensure_ascii=False)

        # 判断是否需要提前触发寻优
        if should_trigger_optimize(triggered):
            logger.warning("[strategy_monitor] 触发紧急参数寻优")
            fire_alert(
                source="strategy_monitor",
                level="critical",
                message="策略漂移严重，建议立即运行参数寻优",
            )
            # 可选: 直接调用 param_optimizer (阻塞)
            # 当前设计: 只告警，由人工或下一次双周周期执行
            # from param_optimizer import param_optimize_job
            # param_optimize_job()


def nn_retrain_callback() -> Dict:
    """
    NN 重训练完成后的回调。

    动作:
    1. 保存当前参数的基线快照
    2. 生成按优先级排序的搜索空间
    3. 触发第 1 轮寻优
    4. 返回搜索空间信息
    """
    # 保存重训练前的基线
    current_params = load_params()
    logger.info("[nn_retrain] 保存重训练前基线快照")

    # 快速计算当前基线指标
    snapshot = _compute_window_metrics(days=60)
    if snapshot:
        save_baseline_snapshot(
            snapshot.metrics,
            label=f"pre_retrain_{datetime.now(timezone.utc).strftime('%Y%m%d')}",
        )

    # 生成搜索空间
    search_spaces = generate_retrain_search_space()

    for round_key, space in search_spaces.items():
        save_search_space(round_key, space)

    logger.info(
        f"[nn_retrain] 搜索空间已生成: "
        f"round1={len(search_spaces['round1_nn_core']['params'])}参数, "
        f"round2={len(search_spaces['round2_extended']['params'])}参数, "
        f"round3={len(search_spaces['round3_full']['params'])}参数"
    )

    return {
        "action": "search_spaces_generated",
        "spaces": {
            k: {"params": list(v["params"].keys()), "description": v["description"]}
            for k, v in search_spaces.items()
        },
        "next_step": "运行第1轮寻优(round1_nn_core)，只搜NN核心5个参数",
    }


def get_iteration_status() -> Dict:
    """获取当前迭代状态"""
    # 检查已有快照和寻优结果
    has_snapshot = os.path.exists(SNAPSHOT_PATH)
    has_optimizer = os.path.exists(os.path.join(MONITOR_DIR, "../optimizer/search_results.json"))
    has_drift_log = os.path.exists(DRIFT_LOG_PATH)

    drift_log = _load_drift_log() if has_drift_log else {}
    baseline = load_baseline_snapshot()

    current_round = 1
    if baseline and len(baseline.get("metrics", {})) > 5:
        # 如果已经有较完整的基线，可能在第2轮
        current_round = 2
    if has_optimizer and baseline:
        current_round = 3

    return {
        "current_round": current_round,
        "has_baseline": has_snapshot,
        "has_optimization_history": has_optimizer,
        "drift_alerts_active": len(drift_log),
        "plans": [
            {
                "round": p.round_num,
                "title": p.title,
                "focus_params": list(p.focus_params),
                "goal": p.goal,
                "success_criteria": p.success_criteria,
            }
            for p in ITERATION_PLANS
        ],
    }
