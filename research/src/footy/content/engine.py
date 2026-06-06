from typing import Dict, Any, Optional
from sqlalchemy.orm import Session
import json

# 尝试导入 backend 的模型，如果路径不对则需要调整 sys.path
import sys
import os
_root = os.path.dirname(os.path.abspath(__file__))
# 向上跳几级到项目根目录，再进入 backend
_backend_root = os.path.abspath(os.path.join(_root, "../../../..", "backend"))
if _backend_root not in sys.path:
    sys.path.append(_backend_root)
    # 模拟 backend/main.py 的路径初始化逻辑
    for d in ["api", "core", "features", "ingestion", "database", "strategy", "monitor", "utils", "api/routers"]:
        sys.path.append(os.path.join(_backend_root, d))

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
        
        if spf_pred:
            probs = spf_pred.probabilities
            model_version = spf_pred.model_version
        else:
            # 💡 核心修复：如果数据库没有预测记录，则调用 PredictionEngine 进行实时计算
            try:
                from core.prediction_engine import PredictionEngine, build_context_from_match
                # 注意：这里需要确保 core 包在路径中，或者使用绝对导入
                # 已经在 init 处处理了 sys.path
                ctx = build_context_from_match(match)
                engine = PredictionEngine(db_session=self.db)
                result = engine.predict(ctx)
                probs = result.spf
                model_version = result.model_version
            except Exception as e:
                import logging
                logging.getLogger("content_engine").error(f"Live prediction failed for match {match_id}: {e}")
                probs = {"home": 0.33, "draw": 0.34, "away": 0.33}
                model_version = "v0.0-fallback"

        # 2. 提取核心指标 (xG, Elo, Form)
        home_xg = home.avg_xg
        away_xg = away.avg_xg
        home_elo = home.elo
        away_elo = away.elo

        # 3. 球员洞察
        home_stars = self._get_top_players(home.id)
        away_stars = self._get_top_players(away.id)

        # 4. 智能文案生成逻辑 (Robust Version)
        # 💡 逻辑重构：不再因为 xG 缺失就回退到冷启动文案。
        # 只要有模型预测结果（probs），就根据概率分位生成研判。
        
        home_win_p = probs["home"]
        away_win_p = probs["away"]
        
        if home_win_p > 0.65:
            headline = f"{home.name} 展现统治级实力优势"
        elif home_win_p > 0.50:
            headline = f"模型看好 {home.name} 主场取胜"
        elif away_win_p > 0.50:
            headline = f"系统预警 {away.name} 具有爆冷潜力"
        elif abs(home_win_p - away_win_p) < 0.10:
            headline = "强强对话：均势博弈局面"
        else:
            headline = "实力均衡的深度对决"

        # 构造深度洞察
        insight = (
            f"基于 {model_version} 引擎分析，本场比赛 {home.name} 对阵 {away.name}。 "
            f"AI 模型计算出的主胜概率为 {home_win_p:.1%}，"
            f"Elo 实力分差为 {abs(elo_diff or 0)}。 "
        )
        
        if home_xg and home_xg > 0:
            insight += f"主队近期场均预期进球 (xG) 为 {home_xg:.2f}，进攻火力稳定。 "
        else:
            insight += "由于主队近期战术特征正在重构，模型目前主要参考 Elo 长期战力权重。 "
            
        if home_stars:
            star = home_stars[0]
            insight += f"{home.name} 核心 {star['name']} 的发挥将是决定比赛走向的关键。"

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
                "data_status": "complete" if has_full_data else "partial",
                "model_version": model_version
            },
            "content": {
                "headline": headline,
                "insight": insight,
                "tags": ["AI分析", "2026世界杯", model_version] if has_full_data else ["系统重构中", "市场研判"]
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
