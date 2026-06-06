from typing import Dict, Any, Optional
from sqlalchemy.orm import Session
import json

# 尝试导入 backend 的模型，如果路径不对则需要调整 sys.path
import sys
import os
_root = os.path.dirname(os.path.abspath(__file__))
# 向上跳几级到项目根目录，再进入 backend
sys.path.append(os.path.abspath(os.path.join(_root, "../../../..", "backend")))

from database.models import Match, Team, Prediction, PlayType, PlayerStats

class WorldCupContentEngine:
    """
    2026 世界杯赛事内容引擎。
    将生产数据库中的原始概率与统计数据转化为结构化的内容卡片。
    """
    def __init__(self, db: Session):
        self.db = db

    def _get_top_players(self, team_id: int, limit: int = 3) -> list:
        """获取球队表现最突出的球员统计"""
        players = self.db.query(PlayerStats).filter(
            PlayerStats.team_id == team_id
        ).order_by(PlayerStats.xg.desc()).limit(limit).all()
        
        return [
            {
                "name": p.player_name,
                "goals": p.goals,
                "assists": p.assists,
                "xg": p.xg,
                "xa": p.xa
            } for p in players
        ]

    def generate_match_preview(self, match_id: int) -> Optional[Dict[str, Any]]:
        """
        从数据库获取真实数据，生成单场比赛的前瞻数据卡。
        """
        match = self.db.query(Match).filter(Match.id == match_id).first()
        if not match:
            return None

        home = match.home_team
        away = match.away_team

        # 1. 获取模型预测
        spf_pred = self.db.query(Prediction).filter(
            Prediction.match_id == match_id,
            Prediction.play_type == PlayType.SPF
        ).first()
        
        probs = spf_pred.probabilities if spf_pred else {"home": 0.33, "draw": 0.34, "away": 0.33}

        # 2. 提取核心指标 (xG, Elo, Form)
        home_xg = home.avg_xg
        away_xg = away.avg_xg
        home_elo = home.elo
        away_elo = away.elo

        # 3. 球员洞察
        home_stars = self._get_top_players(home.id)
        away_stars = self._get_top_players(away.id)

        # 4. 智能文案生成逻辑 (Robust Version)
        has_full_data = (home_xg is not None and home_xg > 0) and (home_elo is not None)
        
        if not has_full_data:
            headline = f"{match.competition} 深度数据建模中"
            insight = (
                f"系统正在对 {home.name} 和 {away.name} 的战术特征进行冷启动重构。 "
                f"目前主要基于市场赔率偏离值进行实时研判。 "
                f"AI 概率显示 {'主队受看好' if probs['home'] > 0.4 else '双方处于均势'}。"
            )
        else:
            elo_diff = (home_elo or 1500) - (away_elo or 1500)
            headline = "实力均衡的对决"
            if elo_diff > 150:
                headline = f"{home.name} 占据绝对实力优势"
            elif elo_diff < -150:
                headline = f"{away.name} 客场反客为主压力大"
            
            insight = (
                f"本场比赛由 {home.name} 对阵 {away.name}。 "
                f"主队场均 xG 为 {home_xg:.2f}，客队为 {away_xg:.2f}。 "
                f"从 Elo 等级分来看，双方分差为 {abs(elo_diff)} 分。 "
            )
            
            if home_stars:
                star = home_stars[0]
                insight += f"{home.name} 核心球员 {star['name']} 在历史数据中表现出色。 "

        # 5. 构造标准卡片 JSON (确保不返回 None 给前端)
        card_data = {
            "match_info": {
                "id": match.id,
                "code": match.match_code,
                "pairing": f"{home.name} vs {away.name}",
                "venue": match.venue or "TBD",
                "kickoff": match.kickoff_at.isoformat() if match.kickoff_at else None
            },
            "teams": {
                "home": {
                    "name": home.name,
                    "elo": home_elo or 1500,
                    "xg": home_xg or 0.0,
                    "rank": home.fifa_rank,
                    "stars": home_stars
                },
                "away": {
                    "name": away.name,
                    "elo": away_elo or 1500,
                    "xg": away_xg or 0.0,
                    "rank": away.fifa_rank,
                    "stars": away_stars
                }
            },
            "quant": {
                "elo_diff": (home_elo or 1500) - (away_elo or 1500),
                "probabilities": probs,
                "confidence": match.confidence or "medium",
                "data_status": "complete" if has_full_data else "partial"
            },
            "content": {
                "headline": headline,
                "insight": insight,
                "tags": ["AI分析", "2026世界杯"] if has_full_data else ["系统重构中", "市场研判"]
            }
        }
        return card_data

    def batch_generate_previews(self, limit: int = 10) -> list:
        """批量生成待开赛场次的前瞻"""
        from database.models import MatchStatus
        matches = self.db.query(Match).filter(
            Match.status == MatchStatus.UPCOMING
        ).limit(limit).all()
        
        return [self.generate_match_preview(m.id) for m in matches]
