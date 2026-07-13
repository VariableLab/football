"""
分层策略可配置参数 — 所有阈值、赔率区间、置信门槛统一定义

设计原则:
1. 框架固定，参数完全不锁死
2. 后期可接入自动遍历寻优，批量跑不同参数组合
3. 模型变、参数就能自动跟着寻优适配
"""
from dataclasses import dataclass
import json
import os

from utils.logger import get_logger

logger = get_logger("strategy_config")

CONFIG_DIR = "./data/strategy"
CONFIG_PATH = os.path.join(CONFIG_DIR, "params.json")
os.makedirs(CONFIG_DIR, exist_ok=True)


@dataclass(frozen=True)
class StrategyParams:
    """
    分层策略全部可配置参数。

    三层框架: skip -> medium_value -> high_value
    筛选逻辑只依赖这些参数，逻辑代码不硬编码任何数字。
    """

    # --- 粗筛层 (skip 判定) ---
    odds_min: float = 1.10
    odds_max: float = 15.0
    min_top_confidence: float = 0.30
    require_odds: bool = True

    # --- NN 置信分层 ---
    nn_high_threshold: float = 0.45
    nn_medium_threshold: float = 0.40

    # --- high_value 额外条件 ---
    draw_odds_min: float = 3.0
    draw_odds_max: float = 3.3
    high_value_min_model_conf: float = 0.40
    high_value_min_edge: float = 0.03

    # --- medium_value 条件 ---
    medium_value_min_model_conf: float = 0.35

    # --- 优化1: 平局独立信号 ---
    draw_signal_nn_threshold: float = 0.40
    draw_signal_odds_min: float = 2.80
    draw_signal_odds_max: float = 4.00
    # 赔率死区: 3.0-3.5命中率28.4%<平衡30.1%, 跳过
    draw_signal_odds_deadzone_min: float = 3.00
    draw_signal_odds_deadzone_max: float = 3.50
    draw_signal_min_spf_prob: float = 0.18

    # --- 优化2: 低赔率主胜降权 ---
    low_odds_home_skip_threshold: float = 1.50
    low_odds_home_damp_threshold: float = 1.80

    # --- 优化3: 赔率区间自适应仓位 ---
    odds_position_map: str = ""

    # --- 5种玩法各自的置信门槛 ---
    spf_min_confidence: float = 0.35
    handicap_min_confidence: float = 0.35
    score_min_confidence: float = 0.05
    goals_min_confidence: float = 0.05
    halftime_min_confidence: float = 0.35

    # --- 仓位建议 ---
    high_position_ratio: float = 1.0
    medium_position_ratio: float = 0.5
    skip_position_ratio: float = 0.0

    # --- 竞彩抽水率 ---
    jingcai_vig_rate: float = 0.13
    normal_vig_rate: float = 0.05

    # --- 版本 ---
    version: str = "tiered_v2"


# --- 默认赔率仓位映射 (优化3) ---
DEFAULT_ODDS_POSITION_MAP = [
    [1.01, 1.50, 0.0],   # 极低赔率: 跳过
    [1.50, 1.80, 0.2],   # 低赔率: 轻仓 (EV=-3.07%)
    [1.80, 2.20, 0.7],   # 中赔率: 标准仓 (EV=+0.33%)
    [2.20, 3.00, 0.5],   # 中高赔率: 半仓 (EV=-0.23%)
    [3.00, 5.00, 0.6],   # 高赔率: 加仓 (EV=+1.65%)
    [5.00, 100.0, 0.3],  # 极高赔率: 轻仓
]


def get_odds_position_map(params: StrategyParams) -> list:
    """解析赔率仓位映射"""
    if params.odds_position_map:
        try:
            return json.loads(params.odds_position_map)
        except (json.JSONDecodeError, TypeError):
            pass
    return DEFAULT_ODDS_POSITION_MAP


def compute_position_ratio(
    tier: str,
    recommended_sel: str,
    recommended_odds: float,
    params: StrategyParams,
) -> float:
    """
    计算自适应仓位系数。

    综合考虑:
    1. 场次等级 (high/medium/skip)
    2. 低赔率主胜降权 (优化2)
    3. 赔率区间自适应 (优化3)
    """
    from strategy.tiered_strategy import TIER_HIGH, TIER_SKIP

    if tier == TIER_SKIP:
        return 0.0

    base = params.high_position_ratio if tier == TIER_HIGH else params.medium_position_ratio

    # 优化2: 低赔率主胜降权
    if recommended_sel == "home":
        if recommended_odds < params.low_odds_home_skip_threshold:
            return 0.0
        if recommended_odds < params.low_odds_home_damp_threshold:
            base *= 0.3

    # 优化3: 赔率区间自适应
    position_map = get_odds_position_map(params)
    odds_ratio = base
    for lo, hi, ratio in position_map:
        if lo <= recommended_odds < hi:
            odds_ratio = ratio
            break

    return round(base * odds_ratio, 2)


# --- 配置管理 ---

DEFAULT_PARAMS = StrategyParams()


def load_params(path: str = CONFIG_PATH) -> StrategyParams:
    """从 JSON 文件加载参数"""
    if not os.path.exists(path):
        return DEFAULT_PARAMS

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return StrategyParams(**{k: v for k, v in data.items() if k in StrategyParams.__dataclass_fields__})
    except (json.JSONDecodeError, TypeError) as e:
        logger.warning(f"[strategy_config] 加载参数失败，使用默认值: {e}")
        return DEFAULT_PARAMS


def save_params(params: StrategyParams, path: str = CONFIG_PATH) -> None:
    """保存参数到 JSON 文件"""
    data = {
        k: getattr(params, k)
        for k in StrategyParams.__dataclass_fields__
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    logger.info(f"[strategy_config] 参数已保存到 {path}")


def update_params(**overrides) -> StrategyParams:
    """更新部分参数并保存"""
    current = load_params()
    data = {k: getattr(current, k) for k in StrategyParams.__dataclass_fields__}
    for k, v in overrides.items():
        if k in data:
            data[k] = v
    new_params = StrategyParams(**data)
    save_params(new_params)
    return new_params
