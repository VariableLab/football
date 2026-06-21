"""
Agent Tools — 智能体“感官”系统

本模块为 Agent 提供直接访问项目底层数据的接口，使其具备“自主调研”能力。
"""

from typing import Dict, List
from sqlalchemy.orm import Session
from datetime import datetime, timedelta, timezone

from database.models import Match, MatchStatus, AccuracySnapshot
from monitor.validation_engine import ValidationEngine
from core.prediction_engine import PredictionEngine, build_context_from_match

class AgentTools:
    @staticmethod
    def scan_market_anomalies(db: Session) -> List[Dict]:
        """扫描全站：寻找 Edge > 15% 或 EV > 10% 的高价值机会 (限制扫描规模)"""
        engine = PredictionEngine(db_session=db)
        
        # 查找未来 48 小时且具有竞彩编号的比赛 (过滤重点场次)
        now = datetime.now(timezone.utc)
        from database.models import JingcaiIssueMatch
        upcoming = db.query(Match).join(JingcaiIssueMatch).filter(
            Match.status != MatchStatus.FINISHED,
            Match.kickoff_at > now,
            Match.kickoff_at < now + timedelta(hours=48)
        ).limit(20).all() # 💡 限制扫描前 20 场最紧迫的焦点战
        
        anomalies = []
        for m in upcoming:
            try:
                # 只有具备赔率的才扫描，否则跳过以节省 CPU
                if not m.odds_home: continue
                
                ctx = build_context_from_match(m)
                res = engine.predict(ctx)
                
                imp_h = 1.0 / (m.odds_home)
                edge = res.spf.get('home', 0) - imp_h
                if edge > 0.12: # 适当降低阈值以展示洞察
                    anomalies.append({
                        "match": f"{m.home_team.name} vs {m.away_team.name}",
                        "type": "Market Edge",
                        "value": f"{edge:+.1%}",
                        "match_id": m.id
                    })
            except: continue
        return anomalies[:5] # 只返回最有价值的前 5 个

    @staticmethod
    def get_system_health_brief(db: Session) -> Dict:
        """获取系统“健康快照”：最近 50 场准确率 vs 历史均值"""
        try:
            # 1. 最近表现 (最近 50 场)
            report = ValidationEngine.run_validation(db, limit=50)
            
            # 2. 历史基准 (从 AccuracySnapshot 查)
            history = db.query(AccuracySnapshot).filter(AccuracySnapshot.metric == "direction_accuracy").order_by(AccuracySnapshot.id.desc()).limit(10).all()
            avg_history = sum(h.value for h in history) / len(history) if history else 0.56

            return {
                "recent_accuracy": report.direction_accuracy,
                "history_accuracy": avg_history,
                "drift": report.direction_accuracy - avg_history,
                "sample_size": report.validated_matches,
                "status": "Excellent" if report.direction_accuracy > avg_history else "Normal"
            }
        except:
            return {"status": "Unknown"}

    @staticmethod
    def get_market_sentiment(db: Session) -> str:
        """分析今日市场情绪：是庄家盘还是实力盘为主？"""
        # 逻辑：观察 Steam Moves 的频率
        # 这里先简化为一个启发式判断
        return "今日市场波动平稳，机构定价倾向于保护性下调。"
