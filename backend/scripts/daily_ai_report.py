"""
daily_ai_report.py - AI 精算师每日自动化推送流

功能:
1. 抓取当天最热门的 3-5 场比赛（包含赔率、基本面、伤病等）。
2. 调用配置好的大模型，注入硬核 Prompt。
3. 输出纯粹的胜平负推荐与盘口深度分析（支持导出为 Markdown 供社交媒体分发）。
"""

import sys
import os
import asyncio
import httpx
from datetime import datetime, timedelta, timezone

# 确保能导入 backend 下的模块
_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _root not in sys.path:
    sys.path.append(_root)

from database.models import SessionLocal, Match, Team, MatchStatus
from database.config import get_settings
from core.prediction_engine import PredictionEngine, build_context_from_match

settings = get_settings()

def get_hot_matches(db, limit=3):
    """获取今天即将进行的几场热门赛事（带赔率）"""
    now = datetime.now(timezone.utc)
    end_of_day = now + timedelta(days=1.5) # 包含明天的早场
    
    matches = db.query(Match).filter(
        Match.status.in_([MatchStatus.SCHEDULED, MatchStatus.UPCOMING]),
        Match.kickoff_at >= now,
        Match.kickoff_at <= end_of_day,
        Match.odds_home.isnot(None)
    ).order_by(Match.kickoff_at.asc()).limit(limit).all()
    
    return matches

async def generate_daily_report():
    print("=" * 60)
    print("🤖 启动 AI 足彩精算师自动化分析流...")
    print("=" * 60)
    
    db = SessionLocal()
    try:
        matches = get_hot_matches(db, limit=3)
        if not matches:
            print("没有找到符合条件的热门赛事，请确认数据库中是否有最近的开盘比赛。")
            return
            
        print(f"✅ 找到 {len(matches)} 场焦点赛事，正在进行数据萃取...\n")
        
        async with httpx.AsyncClient() as client:
            for match in matches:
                home: Team = match.home_team
                away: Team = match.away_team
                
                # 1. 萃取比赛特征数据
                engine = PredictionEngine(db_session=db)
                ctx = build_context_from_match(match)
                res = engine.predict(ctx)
                
                # 2. 构造球队基本面数据 (容错处理)
                def format_team_data(t: Team):
                    return f"""
  - 近期战绩: {t.recent_results if t.recent_results else '暂无数据'}
  - 场均进球/失球: {t.avg_goals_scored or 0}/{t.avg_goals_conceded or 0}
  - 伤病情况: {t.key_injuries if t.key_injuries else '全员健康'}
  - 实力评分 (Elo): {t.elo or '未知'}"""
                
                match_info = f"""
[赛事基本面]
对阵: {home.name} (主) vs {away.name} (客)
联赛: {match.competition or '未知'}
时间: {match.kickoff_at.strftime('%Y-%m-%d %H:%M') if match.kickoff_at else 'TBD'}
当前盘口(欧赔): 胜 {match.odds_home} | 平 {match.odds_draw} | 负 {match.odds_away}
系统测算真实胜率: 主胜 {res.spf.get('home',0):.1%} | 平局 {res.spf.get('draw',0):.1%} | 客胜 {res.spf.get('away',0):.1%}

[主队概况 - {home.name}]{format_team_data(home)}

[客队概况 - {away.name}]{format_team_data(away)}
"""
                
                # 3. 核心 Prompt 设计
                prompt = f"""你是一个精通欧洲五大联赛的专业足彩精算师。请根据以下数据，从进攻、防守、战意三个维度进行极简分析，并最终给出一个明确的预测方向（胜/平/负）和预测比分。要求：语言风格要犀利、专业，像懂球帝的资深专栏作家。不要废话。文末加上一句：“👇 更多每日 VIP 核心推介，请关注主页加入内部私域频道”。

{match_info}
"""
                
                print(f"⏳ 正在推理: {home.name} vs {away.name}...")
                
                try:
                    resp = await client.post(
                        settings.ADVISOR_API_URL,
                        headers={"Authorization": f"Bearer {settings.ADVISOR_API_KEY}"},
                        json={
                            "model": settings.ADVISOR_MODEL,
                            "messages": [{"role": "user", "content": prompt}],
                            "temperature": 0.5 # 稍微调低温度，让精算师的输出更稳重严谨
                        },
                        timeout=45.0
                    )
                    resp.raise_for_status()
                    analysis = resp.json()["choices"][0]["message"]["content"]
                    
                    print("\n" + "=" * 60)
                    print(f"🔥 【{home.name} vs {away.name}】 深度精算报告")
                    print("=" * 60)
                    print(analysis)
                    print("=" * 60 + "\n")
                    
                    await asyncio.sleep(2) # 增加延迟避免 API 速率限制
                    
                except Exception as e:
                    print(f"❌ 推理失败 ({home.name} vs {away.name}): {e}")
                    
    finally:
        db.close()
        print("🏁 今日自动化推送流执行完毕。")

if __name__ == "__main__":
    asyncio.run(generate_daily_report())
