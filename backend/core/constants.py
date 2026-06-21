"""
预测引擎全局常量 — 从 prediction_engine.py 中提取,避免循环导入。

所有子模型从这里导入常量,而不是从 prediction_engine.py。
"""
import os
import yaml
from functools import lru_cache

_CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "model_config.yaml"
)

@lru_cache(maxsize=1)
def load_config():
    """加载 model_config.yaml,带默认值回退"""
    defaults = {
        "MAX_GOALS": 8,
        "POISSON_TRUNCATE": 0.999,
        "HOME_ADVANTAGE_ELO": 0,
        "FORM_WINDOW_MATCHES": 10,
        "DIXON_COLES_RHO": 0.0092,
        "DRAW_INFLATION_FACTOR": 1.35,
        "DEFAULT_WEIGHTS": {
            "elo": 0.35,
            "poisson": 0.35,
            "players": 0.05,
            "market": 0.25,
        },
    }
    if os.path.exists(_CONFIG_PATH):
        try:
            with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
                external = yaml.safe_load(f)
                if external:
                    defaults.update(external)
        except Exception:
            pass
    return defaults

_CFG = load_config()

# 基础常量
MAX_GOALS: int = _CFG["MAX_GOALS"]
POISSON_TRUNCATE: float = _CFG["POISSON_TRUNCATE"]
HOME_ADVANTAGE_ELO: int = _CFG["HOME_ADVANTAGE_ELO"]
FORM_WINDOW_MATCHES: int = _CFG["FORM_WINDOW_MATCHES"]
DIXON_COLES_RHO: float = _CFG["DIXON_COLES_RHO"]
DRAW_INFLATION_FACTOR: float = _CFG["DRAW_INFLATION_FACTOR"]
DEFAULT_WEIGHTS: dict = _CFG["DEFAULT_WEIGHTS"]

# 半场-全场转移矩阵
HT_FT_TRANSITION: dict = _CFG.get("HT_FT_TRANSITION", {
    "home":   {"home": 0.785, "draw": 0.151, "away": 0.065},
    "draw":   {"home": 0.442, "draw": 0.237, "away": 0.321},
    "away":   {"home": 0.105, "draw": 0.199, "away": 0.697},
})

# 半场分布先验
HT_DISTRIBUTION: dict = _CFG.get("HT_DISTRIBUTION", {
    "home": 0.368, "draw": 0.364, "away": 0.268,
})

# 半场时间比例
HALF_TIME_RATIO: float = _CFG.get("HALF_TIME_RATIO", 0.48)

# 淘汰赛进球因子
KNOCKOUT_GOAL_FACTORS: dict = _CFG.get("KNOCKOUT_GOAL_FACTORS", {
    "R16": 0.88, "QF": 0.85, "SF": 0.82, "F": 0.80, "3P": 0.90,
})

# 战术矩阵
TACTICAL_MATRIX: dict = _CFG.get("TACTICAL_MATRIX", {
    "attack":    {"attack": (1.15, 1.15), "defense": (0.88, 0.72), "balanced": (1.02, 0.92), "counter": (0.95, 1.12)},
    "defense":   {"attack": (0.72, 0.88), "defense": (0.68, 0.68), "balanced": (0.80, 0.85), "counter": (0.62, 0.75)},
    "balanced":  {"attack": (0.92, 1.02), "defense": (0.85, 0.80), "balanced": (1.00, 1.00), "counter": (0.95, 0.95)},
    "counter":   {"attack": (1.12, 0.95), "defense": (0.75, 0.62), "balanced": (0.95, 0.95), "counter": (0.85, 0.85)},
})

# 天气/场地惩罚
WEATHER_PENALTY: dict = _CFG.get("WEATHER_PENALTY", {
    "clear": 0.00, "cloudy": 0.00, "rain": 0.04, "hot": 0.06, "cold": 0.04, "snow": 0.10,
})
PITCH_PENALTY: dict = _CFG.get("PITCH_PENALTY", {
    "good": 0.00, "average": 0.02, "poor": 0.05, "artificial": 0.03,
})

# 休息天数惩罚
REST_PENALTY: dict = _CFG.get("REST_PENALTY", {
    (5, 999): 1.00, (3, 5): 0.97, (2, 3): 0.92, (0, 2): 0.85,
})

# 球员位置影响
PLAYER_POSITION_IMPACT: dict = _CFG.get("PLAYER_POSITION_IMPACT", {
    "goalkeeper": 0.12, "defense": 0.08, "midfield": 0.07, "forward": 0.08,
})

# 临场异动修正
STEAM_MOVE: dict = _CFG.get("STEAM_MOVE", {
    "min_intensity": 0.05, "max_intensity": 0.15, "sigmoid_slope": 20,
    "target_prob_low": 0.50, "target_prob_high": 0.65, "alpha_multiplier": 0.4,
})

# 降级模式
DEGRADED: dict = _CFG.get("DEGRADED", {
    "mock_elo_threshold": 1600,
    "lab_elo_weight_normal": 0.90,
    "lab_elo_weight_degraded": 0.95,
})

# 残差NN融合
RESIDUAL_NN: dict = _CFG.get("RESIDUAL_NN", {
    "lr_weight": 0.4, "nn_weight": 0.6,
})
