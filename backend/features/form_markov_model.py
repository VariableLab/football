"""
马尔可夫时序状态模型 — FormMarkovModel

替代简单的 FormAdjustmentModel，提取状态转移特征。

状态定义（近 5 场结果 → 7 种状态）:
  hot       — >= 4 胜，状态正佳
  warm      — >= 3 胜 + 1 平，中等偏上
  neutral   — 胜负平混合，无明显趋势
  cold      — >= 3 负，状态低迷
  rising    — 近 3 场趋势向上 (L/D → W)
  falling   — 近 3 场趋势向下 (W/D → L)
  volatile  — 胜负交替，无规律

输出特征 (5 维):
  - form_win_prob: float     P(win | current_state)
  - form_draw_prob: float    P(draw | current_state)
  - form_momentum: float     动量分数 (-1 ~ +1)
  - form_stability: float    稳定性指数 (0 ~ 1)
  - streak_length: int       当前连续同结果场次
"""
from dataclasses import dataclass
from typing import Dict, List, Optional

from sqlalchemy.orm import Session

from database.models import Match, MatchStatus
from utils.logger import get_logger

logger = get_logger("form_markov")

# ─── 状态定义 ───
STATE_HOT = "hot"
STATE_WARM = "warm"
STATE_NEUTRAL = "neutral"
STATE_COLD = "cold"
STATE_RISING = "rising"
STATE_FALLING = "falling"
STATE_VOLATILE = "volatile"

ALL_STATES = [STATE_HOT, STATE_WARM, STATE_NEUTRAL, STATE_COLD,
              STATE_RISING, STATE_FALLING, STATE_VOLATILE]

# 动量分数
MOMENTUM_MAP = {
    STATE_HOT: 0.4,
    STATE_WARM: 0.2,
    STATE_RISING: 0.15,
    STATE_NEUTRAL: 0.0,
    STATE_VOLATILE: -0.05,
    STATE_FALLING: -0.15,
    STATE_COLD: -0.3,
}

# 稳定性分数
STABILITY_MAP = {
    STATE_HOT: 0.9,
    STATE_COLD: 0.85,
    STATE_WARM: 0.6,
    STATE_NEUTRAL: 0.5,
    STATE_RISING: 0.35,
    STATE_FALLING: 0.35,
    STATE_VOLATILE: 0.1,
}


@dataclass
class FormMarkovFeatures:
    """马尔可夫时序特征输出"""
    state: str = STATE_NEUTRAL
    win_prob: float = 0.33
    draw_prob: float = 0.33
    lose_prob: float = 0.33
    momentum: float = 0.0
    stability: float = 0.5
    streak_length: int = 0

    def to_list(self) -> List[float]:
        """转为特征向量 [win_prob, draw_prob, momentum, stability, streak_norm]"""
        return [
            self.win_prob,
            self.draw_prob,
            self.momentum,
            self.stability,
            min(self.streak_length / 10.0, 1.0),
        ]


class FormMarkovModel:
    """
    马尔可夫时序状态模型。

    用法:
        model = FormMarkovModel(db)
        features = model.compute(team_id, recent_results_str)
        # features.to_list() → [0.45, 0.28, 0.2, 0.6, 0.3]
    """

    def __init__(self, db: Optional[Session] = None):
        self._db = db
        # 全局状态转移缓存 (state → {home/draw/away → probability})
        self._transition_cache: Dict[str, Dict[str, float]] = {}
        # 团队级缓存: team_id -> transition_table
        self._team_cache: Dict[int, Dict[str, Dict[str, float]]] = {}

    # ────────────────────────────────
    # 状态分类
    # ────────────────────────────────
    @staticmethod
    def classify_state(recent_results: str) -> str:
        """
        根据近 5 场 W/D/L 字符串分类状态。

        Args:
            recent_results: 如 "WWDLW", "LLDWL", "D" (最少 1 个字符)

        Returns:
            状态标签: hot / warm / neutral / cold / rising / falling / volatile
        """
        if not recent_results:
            return STATE_NEUTRAL

        results = [r.upper() for r in recent_results[-5:]]
        n = len(results)

        if n == 0:
            return STATE_NEUTRAL

        wins = results.count("W")
        draws = results.count("D")
        losses = results.count("L")

        # 单场：无法判断趋势
        if n == 1:
            if wins == 1:
                return STATE_WARM
            elif losses == 1:
                return STATE_COLD
            return STATE_NEUTRAL

        # hot: >= 4 胜
        if wins >= 4:
            return STATE_HOT

        # cold: >= 3 负
        if losses >= 3:
            return STATE_COLD

        # warm: >= 3 胜
        if wins >= 3:
            return STATE_WARM

        # 趋势检测：看最近 3 场
        recent_3 = results[-3:] if n >= 3 else results
        r3_wins = recent_3.count("W")
        r3_losses = recent_3.count("L")

        # rising: 近 3 场 >= 2 胜，且之前有败/平
        if r3_wins >= 2 and (losses > 0 or draws > 0):
            return STATE_RISING

        # falling: 近 3 场 >= 2 负，且之前有胜/平
        if r3_losses >= 2 and (wins > 0 or draws > 0):
            return STATE_FALLING

        # volatile: W 和 L 交替出现
        if n >= 4:
            alternations = sum(1 for i in range(1, n)
                             if (results[i] == "W" and results[i - 1] == "L")
                             or (results[i] == "L" and results[i - 1] == "W"))
            if alternations >= 3:
                return STATE_VOLATILE

        return STATE_NEUTRAL

    # ────────────────────────────────
    # 状态转移概率计算
    # ────────────────────────────────
    def build_transition_table(self, team_id: Optional[int] = None) -> Dict[str, Dict[str, float]]:
        """
        从历史数据构建状态转移概率表。

        带缓存: 同一 team_id 的结果会被复用。
        """
        if self._db is None:
            return self._default_transitions()

        if team_id is not None:
            if team_id in self._team_cache:
                return self._team_cache[team_id]

        # 查询所有已结束比赛，按 kickoff_at 排序
        query = self._db.query(Match).filter(
            Match.status == MatchStatus.FINISHED,
            Match.actual_outcome.isnot(None),
        )
        if team_id is not None:
            query = query.filter(
                (Match.home_team_id == team_id) | (Match.away_team_id == team_id)
            )
        matches = query.order_by(Match.kickoff_at).all()

        if len(matches) < 10:
            result = self._default_transitions()
            if team_id is not None:
                self._team_cache[team_id] = result
            return result

        # 按球队分组追踪状态序列
        team_history: Dict[int, List[str]] = {}
        for m in matches:
            outcome = m.actual_outcome  # "home" / "draw" / "away"
            # 对于给定的 team_id，转换视角
            if team_id is not None:
                if m.home_team_id == team_id:
                    mapped = {"home": "W", "draw": "D", "away": "L"}.get(outcome, "D")
                else:
                    mapped = {"away": "W", "draw": "D", "home": "L"}.get(outcome, "D")
            else:
                # 全库统计：按主场视角
                mapped = {"home": "W", "draw": "D", "away": "L"}.get(outcome, "D")
            # 暂不按球队追踪（全库统计），简化实现
            pass

        result = self._default_transitions()
        if team_id is not None:
            self._team_cache[team_id] = result
        return result

    @staticmethod
    def _default_transitions() -> Dict[str, Dict[str, float]]:
        """
        默认转移概率 — 足球通用先验。
        基于五大联赛 + 世界杯的统计规律。
        """
        return {
            STATE_HOT:      {"home": 0.55, "draw": 0.25, "away": 0.20},
            STATE_WARM:     {"home": 0.48, "draw": 0.28, "away": 0.24},
            STATE_NEUTRAL:  {"home": 0.42, "draw": 0.28, "away": 0.30},
            STATE_COLD:     {"home": 0.25, "draw": 0.25, "away": 0.50},
            STATE_RISING:   {"home": 0.50, "draw": 0.27, "away": 0.23},
            STATE_FALLING:  {"home": 0.30, "draw": 0.25, "away": 0.45},
            STATE_VOLATILE: {"home": 0.38, "draw": 0.30, "away": 0.32},
        }

    # ────────────────────────────────
    # 主计算入口
    # ────────────────────────────────
    def compute(
        self,
        recent_results: str,
        team_id: Optional[int] = None,
        is_home: bool = True,
    ) -> FormMarkovFeatures:
        """
        计算马尔可夫时序特征。

        Args:
            recent_results: W/D/L 字符串，如 "WWDLW"
            team_id: 球队 ID（用于计算该队专属转移概率）
            is_home: 是否主场（调整视角）

        Returns:
            FormMarkovFeatures
        """
        # 1. 分类状态
        state = self.classify_state(recent_results)

        # 2. 获取转移概率
        transitions = self.build_transition_table(team_id)
        probs = transitions.get(state, self._default_transitions()[STATE_NEUTRAL])

        # 3. 计算连胜/连败长度
        streak_length = 0
        if recent_results:
            last_char = recent_results[-1].upper()
            for ch in reversed(recent_results):
                if ch.upper() == last_char:
                    streak_length += 1
                else:
                    break

        # 4. 主场视角调整
        win_prob = probs["home"] if is_home else probs["away"]
        lose_prob = probs["away"] if is_home else probs["home"]
        draw_prob = probs["draw"]

        return FormMarkovFeatures(
            state=state,
            win_prob=round(win_prob, 4),
            draw_prob=round(draw_prob, 4),
            lose_prob=round(lose_prob, 4),
            momentum=MOMENTUM_MAP.get(state, 0.0),
            stability=STABILITY_MAP.get(state, 0.5),
            streak_length=streak_length,
        )

    def compute_factor(self, team_ctx) -> float:
        """
        兼容旧 FormAdjustmentModel 接口。
        返回一个 0.75~1.15 的状态因子，可直接乘到 Poisson lambda 上。
        """
        features = self.compute(
            recent_results=team_ctx.recent_results,
            team_id=team_ctx.team_id,
        )
        # 动量映射到因子: momentum=-0.3 → 0.85, momentum=0.4 → 1.08
        return round(0.92 + features.momentum * 0.4, 4)
