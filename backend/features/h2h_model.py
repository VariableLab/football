"""
历史交锋模型 — H2HModel

从数据库查询两队历史交锋记录，提取特征。

输出特征 (6 维):
  - h2h_total: int              历史交锋总场次
  - h2h_home_win_pct: float     主队在历史交锋中的胜率
  - h2h_draw_pct: float         平局率
  - h2h_recent_win_pct: float   近 3 次交锋主队胜率
  - h2h_avg_goals: float        历史交锋场均总进球
  - is_first_meeting: bool      是否首次交锋

降级策略: h2h_total < 3 → 所有特征归零（让融合层忽略）
"""
from dataclasses import dataclass
from typing import List

from sqlalchemy.orm import Session

from database.models import Match, MatchStatus


@dataclass
class H2HFeatures:
    """历史交锋特征"""
    total: int = 0
    home_win_pct: float = 0.0
    draw_pct: float = 0.0
    recent_win_pct: float = 0.0
    avg_goals: float = 0.0
    is_first_meeting: bool = True

    def to_list(self) -> List[float]:
        """转为特征向量，如果数据不足则归零"""
        if self.total < 3 or self.is_first_meeting:
            return [0.0, 0.0, 0.0, 0.0, 0.0, 1.0]
        return [
            min(self.total / 30.0, 1.0),   # 归一化到 [0, 1]，30 场为饱和
            self.home_win_pct,
            self.draw_pct,
            self.recent_win_pct,
            min(self.avg_goals / 5.0, 1.0),  # 场均 5 球为上限
            0.0,  # is_first_meeting=0
        ]


class H2HModel:
    """
    历史交锋模型。

    用法:
        model = H2HModel(db)
        features = model.compute(home_team_id, away_team_id)
    """

    def __init__(self, db: Session):
        self._db = db

    def compute(self, home_team_id: int, away_team_id: int) -> H2HFeatures:
        """
        查询两队历史交锋，计算特征。

        Args:
            home_team_id: 主队 ID
            away_team_id: 客队 ID

        Returns:
            H2HFeatures
        """
        # 查询已结束的交锋记录
        matches = (
            self._db.query(Match)
            .filter(
                Match.status == MatchStatus.FINISHED,
                Match.actual_outcome.isnot(None),
                Match.actual_home_goals.isnot(None),
                Match.actual_away_goals.isnot(None),
                (
                    (Match.home_team_id == home_team_id) & (Match.away_team_id == away_team_id)
                )
                | (
                    (Match.home_team_id == away_team_id) & (Match.away_team_id == home_team_id)
                ),
            )
            .order_by(Match.kickoff_at.desc())
            .all()
        )

        if not matches:
            return H2HFeatures(is_first_meeting=True)

        total = len(matches)

        if total < 3:
            return H2HFeatures(total=total, is_first_meeting=False)

        # 统计指标
        home_wins = 0
        draws = 0
        total_goals = 0
        recent_wins = 0

        for i, m in enumerate(matches):
            # 总进球
            total_goals += (m.actual_home_goals or 0) + (m.actual_away_goals or 0)

            # 判断主队是 home_team_id 还是 away_team_id
            if m.home_team_id == home_team_id:
                is_home_win = m.actual_outcome == "home"
                is_draw = m.actual_outcome == "draw"
            else:
                is_home_win = m.actual_outcome == "away"
                is_draw = m.actual_outcome == "draw"

            if is_home_win:
                home_wins += 1
                if i < 3:
                    recent_wins += 1
            elif is_draw:
                draws += 1

        home_win_pct = home_wins / total
        draw_pct = draws / total
        recent_win_pct = recent_wins / min(3, total)
        avg_goals = total_goals / total

        return H2HFeatures(
            total=total,
            home_win_pct=round(home_win_pct, 4),
            draw_pct=round(draw_pct, 4),
            recent_win_pct=round(recent_win_pct, 4),
            avg_goals=round(avg_goals, 2),
            is_first_meeting=False,
        )
