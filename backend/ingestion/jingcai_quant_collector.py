"""
JingcaiQuantCollector — 竞彩专用量化数据采集器

功能:
1. 监听 sporttery.cn 赔率流，记录每分钟级 SP 异动
2. 将 SP 异动转化为量化特征 (Market Momentum)
3. 记录期号生命周期中的关键资金流入信号
"""
from datetime import datetime, timezone, timedelta

from database.models import SessionLocal, Match, OddsHistory
from ingestion.odds_collector import JingcaiSource
from utils.logger import get_logger

logger = get_logger("jingcai_quant")

class JingcaiQuantCollector:
    def __init__(self):
        self.db = SessionLocal()
        self.src = JingcaiSource()

    def capture_sp_fluctuations(self):
        """核心任务：捕捉并记录当前在售竞彩比赛的 SP 波动"""
        try:
            today = datetime.now().strftime("%Y-%m-%d")
            # 仅获取未来 24h 内的比赛，确保高频采集的效率
            end_date = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
            
            logger.info(f"[quant] 正在采集 {today} 竞彩实时 SP 流...")
            api_data = self.src._fetch_all_pools(today, end_date)
            
            if not api_data:
                return
            
            now = datetime.now(timezone.utc)
            recorded_count = 0
            
            for mid, mdata in api_data.items():
                had = mdata.get("had", {})
                hhad = mdata.get("hhad", {})
                
                # 1. 提取基础 SP
                sp_h = self._safe_float(had.get("h"))
                sp_d = self._safe_float(had.get("d"))
                sp_a = self._safe_float(had.get("a"))
                
                if not sp_h: continue

                # 2. 匹配数据库 Match
                # 注意：这里需要根据 match_date 和 team 名匹配，简化处理
                match_date = mdata.get("matchDate", "")
                home_cn = mdata.get("homeTeamAbbName", "")
                
                match = self.db.query(Match).filter(
                    Match.kickoff_at >= datetime.strptime(match_date, "%Y-%m-%d"),
                    Match.kickoff_at < datetime.strptime(match_date, "%Y-%m-%d") + timedelta(days=1)
                ).filter(Match.match_code.like(f"JC-{match_date.replace('-', '')}%")).first()
                
                if not match: continue

                # 3. 检查最后一次记录，避免重复写入无变动的数据
                last_history = self.db.query(OddsHistory).filter(
                    OddsHistory.match_id == match.id,
                    OddsHistory.source == "sporttery"
                ).order_by(OddsHistory.recorded_at.desc()).first()
                
                if last_history and last_history.odds_home == sp_h and \
                   last_history.odds_draw == sp_d and last_history.odds_away == sp_a:
                    continue # 无变动，跳过
                
                # 4. 写入异动流水
                history = OddsHistory(
                    match_id=match.id,
                    source="sporttery",
                    odds_home=sp_h,
                    odds_draw=sp_d,
                    odds_away=sp_a,
                    recorded_at=now
                )
                self.db.add(history)
                recorded_count += 1
                
                # 5. 更新 Match 表实时 SP
                match.odds_home = sp_h
                match.odds_draw = sp_d
                match.odds_away = sp_a
                match.odds_source = "sporttery"
                match.updated_at = now

            self.db.commit()
            if recorded_count > 0:
                logger.info(f"[quant] 记录到 {recorded_count} 场比赛 SP 异动")
                
        except Exception as e:
            self.db.rollback()
            logger.error(f"[quant] 采集异常: {e}")
        finally:
            self.db.close()
            self.src.close()

    @staticmethod
    def _safe_float(val):
        try:
            return float(val) if val and float(val) > 0 else None
        except:
            return None

def run_quant_collector_job():
    """供调度器调用的高频入口（建议每 10 分钟运行一次）"""
    collector = JingcaiQuantCollector()
    collector.capture_sp_fluctuations()
