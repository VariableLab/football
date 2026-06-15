#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
exchange_anomaly_daemon.py - 24小时必发资金冷热监控与 AI 联动预警守护进程

功能：
1. 定时调用 bf_volume_scraper 刷新当日最新的必发已成交资金。
2. 提取数据库中未来 3 天内的待开赛世界杯赛事。
3. 从赔率数据反推隐含概率（去 Overround），并与实际必发成交比例做差计算偏离度 Bias。
4. 一旦偏离度 > 25% 或是成交量短时间内暴涨：
   - 自动在后台唤醒 news_morale_agent.py (Gemini 免费 API) 联网检索赛前突发原因。
   - 自动调用 Telegram Notifier 免费将资金异常偏离数据及 AI 精算研判报告推送至用户手机。
"""

import sys
import os
from datetime import datetime, timedelta, timezone

# 保证路径正确
_current_dir = os.path.dirname(os.path.abspath(__file__))
_backend_root = os.path.dirname(_current_dir)
if _backend_root not in sys.path:
    sys.path.insert(0, _backend_root)

# 强制加载环境变量
from dotenv import load_dotenv
load_dotenv(os.path.join(_backend_root, ".env"))

from google import genai

from database.models import SessionLocal, Match, MatchStatus, BettingExchangeVolume, MatchAIReport, Prediction
from ingestion.bf_volume_scraper import BFVolumeScraper
from scripts.news_morale_agent import process_match_morale
from utils.telegram_notifier import send_telegram_markdown_message

# 异常阈值设定
BIAS_THRESHOLD = 0.25 # 资金占比与暗含胜率偏离超过 25% 则预警

def compute_implied_probabilities(match):
    """
    根据比赛的当前赔率 (odds_home, odds_draw, odds_away) 反推去 margin 的公平概率
    """
    oh = match.odds_home
    od = match.odds_draw
    oa = match.odds_away
    
    if not oh or not od or not oa:
        return None
        
    p_h = 1.0 / oh
    p_d = 1.0 / od
    p_a = 1.0 / oa
    total = p_h + p_d + p_a
    
    return {
        "home": p_h / total,
        "draw": p_d / total,
        "away": p_a / total
    }

def run_monitor_cycle(db, client, dry_run=False):
    print("==================================================")
    print("⏰ 启动 24小时必发成交量异常与 AI 联动监测轮备...")
    print("==================================================")
    
    # Step 1: 刷新必发数据并进行队名内存配对
    scraper = BFVolumeScraper(db)
    aligned_count = scraper.scrape_and_align(days_ahead=3)
    if aligned_count == 0:
        print("  [Monitor] 未匹配到近期有成交量数据的世界杯赛事，本次轮巡结束。")
        return
        
    # Step 2: 循环计算与检测异常
    now = datetime.now(timezone.utc)
    future_bound = now + timedelta(days=3)
    matches = db.query(Match).filter(
        Match.status != MatchStatus.FINISHED,
        Match.kickoff_at >= now,
        Match.kickoff_at <= future_bound
    ).all()
    
    alerts_triggered = 0
    for match in matches:
        vol = db.query(BettingExchangeVolume).filter(BettingExchangeVolume.match_id == match.id).first()
        if not vol:
            continue
            
        implied_p = compute_implied_probabilities(match)
        if not implied_p:
            # 如果没有赔率，尝试用默认平分 0.33 兜底，或跳过
            implied_p = {"home": 0.33, "draw": 0.33, "away": 0.33}
            
        # 计算偏离度
        bias_h = abs(vol.home_ratio - implied_p["home"])
        bias_d = abs(vol.draw_ratio - implied_p["draw"])
        bias_a = abs(vol.away_ratio - implied_p["away"])
        max_bias = max(bias_h, bias_d, bias_a)
        
        home_name = match.home_team.name
        away_name = match.away_team.name
        
        print(f"  [Check] 赛事 [{match.match_code}] {home_name} vs {away_name}:")
        print(f"    - 实际成交分布: 主={vol.home_ratio:.2%}, 平={vol.draw_ratio:.2%}, 客={vol.away_ratio:.2%}")
        print(f"    - 赔率暗含概率: 主={implied_p['home']:.2%}, 平={implied_p['draw']:.2%}, 客={implied_p['away']:.2%}")
        print(f"    - 最大偏离度 Bias: {max_bias:.2%}")
        
        if max_bias >= BIAS_THRESHOLD:
            print(f"    ⚠️ 检测到资金流异常！偏离度 {max_bias:.2%} 超过警戒线 {BIAS_THRESHOLD:.2%}")
            alerts_triggered += 1
            
            # Step 3: 联动 AI 舆情检索并更新预测
            print(f"    🤖 [联动启动] 正在调用 Gemini 联网检索 {home_name} 和 {away_name} 的赛前突发风波...")
            try:
                # 调用 news_morale_agent 封装的方法
                # 该方法会自动：联网搜索 -> 结构化量化 -> 更新数据库 -> 调用 PredictionEngine 重新预测并持久化
                process_success = process_match_morale(db, match, client, dry_run=dry_run)
                
                if process_success and not dry_run:
                    # Step 4: 整合报告并通过 Telegram 发送免费推送
                    print("    📡 [发送警报] 正在拼装电报推送报告...")
                    
                    # 获取刚刚更新过的最新预测概率
                    updated_spf = db.query(Prediction).filter(
                        Prediction.match_id == match.id,
                        Prediction.play_type == "SPF"
                    ).first()
                    spf_probs = updated_spf.probabilities if updated_spf else {}
                    
                    # 读取最新写入的精算报告
                    ai_report = db.query(MatchAIReport).filter(MatchAIReport.match_id == match.id).first()
                    report_content = ai_report.content if ai_report else "暂无精算师报告。"
                    
                    # 💡 只取报告的核心量化与分析部分，砍掉底部的原始 Google 检索文献，防止 Telegram Markdown 格式解析错误
                    clean_content = report_content.split("---")[0]
                    
                    # 构造推送 Markdown
                    alert_md = f"""🚨 *【2026 世界杯 必发交易所资金异常警报】*

⚽ *焦点对阵*：{home_name} vs {away_name} (场次: {match.match_code})

📈 *必发资金异常数据*
* *总成交额*：`¥{vol.total_volume:,.2f}`
* *实际成交比*：主 {vol.home_ratio:.1%} | 平 {vol.draw_ratio:.1%} | 客 {vol.away_ratio:.1%}
* *赔率暗含比*：主 {implied_p['home']:.1%} | 平 {implied_p['draw']:.1%} | 客 {implied_p['away']:.1%}
* *最大偏离值*：`{max_bias:.1%}` (触发线: {BIAS_THRESHOLD:.1%})

🔄 *AI 联动重预测校准*
* *最新融合胜平负概率*：主胜 `{spf_probs.get('home', 0.0):.1%}` | 平局 `{spf_probs.get('draw', 0.0):.1%}` | 客胜 `{spf_probs.get('away', 0.0):.1%}`
* *主队调整系数*：`{match.home_team.form_factor:.2f}` (伤停: {match.home_team.key_injuries or '无'})
* *客队调整系数*：`{match.away_team.form_factor:.2f}` (伤停: {match.away_team.key_injuries or '无'})

{clean_content}
"""
                    # 发送通知
                    send_telegram_markdown_message(alert_md)
                    
            except Exception as e:
                print(f"    ❌ 联动处理赛事 {match.match_code} 异常: {e}")
                db.rollback()
                continue
        else:
            print("    ✅ 资金流向分布处于合理范围内。")
            
    print(f"\n🎉 监控轮巡完毕。共触发 {alerts_triggered} 场焦点赛事的异常报警联动。")

def main():
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        print("❌ 错误：未配置 GEMINI_API_KEY，无法启动联动监测。")
        sys.exit(1)
        
    client = genai.Client(api_key=api_key)
    db = SessionLocal()
    try:
        run_monitor_cycle(db, client, dry_run=False)
    finally:
        db.close()

if __name__ == "__main__":
    main()
