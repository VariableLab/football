#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
news_morale_agent.py - 独立的赛前新闻舆情检索与情报量化 Agent

功能：
1. 联网检索即将开赛的焦点赛事的两队最新动态（伤病、备战、士气、主帅发言等）。
2. 使用双阶段（联网 + 结构化）Gemini-2.5-flash 大模型进行精细化评估，量化出士气评分（-1.0 ~ 1.0）。
3. 自动将士气评分映射为 form_factor 更新回对应球队，更新核心伤停 key_injuries。
4. 在 match_ai_reports 生成/更新 Markdown 格式的赛前精算报告。
5. 自动联动 PredictionEngine 刷新该赛事的预测概率，实现真正的物理校准闭环。
"""

import sys
import os
import argparse
import time
from datetime import datetime, timedelta, timezone

# 确保路径定位正确，将 backend 根目录置于 path 中
_current_dir = os.path.dirname(os.path.abspath(__file__))
_backend_root = os.path.dirname(_current_dir)
if _backend_root not in sys.path:
    sys.path.insert(0, _backend_root)

# 强制加载 .env 环境变量
from dotenv import load_dotenv
load_dotenv(os.path.join(_backend_root, ".env"))

from google import genai
from google.genai import types
from pydantic import BaseModel, Field
from sqlalchemy import or_

from database.models import SessionLocal, Match, Team, MatchAIReport, Prediction, MatchStatus
from core.prediction_engine import PredictionEngine, build_context_from_match

# ─── Pydantic Schema 定义 ───
class TeamMorale(BaseModel):
    team_name: str = Field(description="The name of the football team")
    morale_score: float = Field(description="Morale score between -1.0 and 1.0. -1.0 means highly negative morale/crises, 0.0 means neutral, 1.0 means extremely high morale")
    key_injuries: str = Field(description="Comma-separated key injuries or suspensions in Chinese, e.g., '阿劳霍(伤),希门尼斯(伤疑)' or '无'")
    retrieved_sources: str = Field(description="Summary of retrieved news sources used for this rating in Chinese")
    rationale: str = Field(description="Brief explanation of the rationale behind the morale score in Chinese")

class MatchMoraleReport(BaseModel):
    home: TeamMorale = Field(description="Home team morale details")
    away: TeamMorale = Field(description="Away team morale details")
    match_analysis: str = Field(description="Comprehensive match context analysis based on current news in Chinese")

# ─── 指数退避重试装饰器/包装器 ───
def generate_content_with_retry(client, model, contents, config, max_retries=5, initial_backoff=2):
    backoff = initial_backoff
    for attempt in range(max_retries):
        try:
            return client.models.generate_content(
                model=model,
                contents=contents,
                config=config
            )
        except Exception as e:
            if attempt == max_retries - 1:
                raise e
            print(f"  [Warning] API call failed: {e}. Retrying in {backoff} seconds (attempt {attempt+1}/{max_retries})...")
            time.sleep(backoff)
            backoff *= 2

# ─── 核心双阶段量化引擎 ───
def process_match_morale(db, match, client, dry_run=False):
    home_name = match.home_team.name
    away_name = match.away_team.name
    home_en = match.home_team.name_en or home_name
    away_en = match.away_team.name_en or away_name
    
    print(f"\n🚀 开始处理比赛 [{match.match_code}] {home_name} vs {away_name} (ID: {match.id})")
    
    # ─── 阶段 1：联网检索 ───
    print(f"  [Stage 1] 联网检索中...")
    grounding_tool = types.Tool(
        google_search=types.GoogleSearch()
    )
    search_config = types.GenerateContentConfig(
        tools=[grounding_tool],
        temperature=0.3,
    )
    # 使用中英文双语搜索保证覆盖率
    search_prompt = (
        f"Search for recent news (within the last 7 days) about '{home_en}' ('{home_name}') "
        f"and '{away_en}' ('{away_name}') national football teams ahead of their upcoming match in 2026. "
        "Focus on team updates, player injuries, suspensions, team morale, training status, manager press conference, and roster changes. "
        "Provide a detailed factual report in Chinese, listing key facts clearly."
    )
    
    search_response = generate_content_with_retry(
        client=client,
        model="gemini-2.5-flash",
        contents=search_prompt,
        config=search_config
    )
    search_text = search_response.text
    print(f"  [Stage 1] 联网检索成功，已生成 {len(search_text)} 字赛前情报纪要。")
    
    # ─── 阶段 2：结构化量化提取 ───
    print(f"  [Stage 2] 提取结构化数据中...")
    extract_prompt = f"""
You are an expert football data analyst. Analyze the following news report about the upcoming match between {home_name} (Home) and {away_name} (Away).
Based ONLY on the provided news context, extract the structured information.

News Context:
\"\"\"
{search_text}
\"\"\"

Please strictly populate the schema.
For morale_score:
- A float between -1.0 and 1.0. 
- Positive (>0) means good news (key players returning, high team confidence, good preparation).
- Negative (<0) means crises (severe key injuries, team internal conflicts, poor form, travel disruption).
- 0.0 means neutral or no major news.
For key_injuries:
- Format as comma-separated string in Chinese, e.g. "梅西(伤),内马尔(停)" or "无" if there are none. Translate player names and status to Chinese.
"""
    extract_config = types.GenerateContentConfig(
        response_mime_type="application/json",
        response_schema=MatchMoraleReport,
        temperature=0.1,
    )
    
    extract_response = generate_content_with_retry(
        client=client,
        model="gemini-2.5-flash",
        contents=extract_prompt,
        config=extract_config
    )
    
    report: MatchMoraleReport = extract_response.parsed
    
    home_morale = report.home.morale_score
    away_morale = report.away.morale_score
    
    # 映射士气为状态系数 0.5 ~ 1.5
    home_factor = min(max(1.0 + home_morale, 0.5), 1.5)
    away_factor = min(max(1.0 + away_morale, 0.5), 1.5)
    
    print(f"  [Results] 主队 [{home_name}]: 士气评分={home_morale:+.2f} -> 修正系数={home_factor:.2f}, 伤病: {report.home.key_injuries}")
    print(f"  [Results] 客队 [{away_name}]: 士气评分={away_morale:+.2f} -> 修正系数={away_factor:.2f}, 伤病: {report.away.key_injuries}")
    
    if dry_run:
        print("  [Dry Run] 跳过数据库保存与预测重新计算。")
        return True
    
    # ─── 数据库更新 ───
    # 1. 球队状态更新
    match.home_team.form_factor = home_factor
    match.home_team.key_injuries = report.home.key_injuries
    
    match.away_team.form_factor = away_factor
    match.away_team.key_injuries = report.away.key_injuries
    
    # 2. 赛前精算报告生成
    report_md = f"""# AI 精算师赛前情报量化报告：{home_name} vs {away_name}

## 📊 舆情士气量化评估

* **{home_name} (主队)**
  * **舆情士气指数**：`{home_morale:+.2f}` (计算得出的状态因子：`{home_factor:.2f}`)
  * **核心伤停人员**：{report.home.key_injuries}
  * **评分依据**：{report.home.rationale}
  * **检索事实摘要**：{report.home.retrieved_sources}

* **{away_name} (客队)**
  * **舆情士气指数**：`{away_morale:+.2f}` (计算得出的状态因子：`{away_factor:.2f}`)
  * **核心伤停人员**：{report.away.key_injuries}
  * **评分依据**：{report.away.rationale}
  * **检索事实摘要**：{report.away.retrieved_sources}

## 🔍 综合情报 analysis
{report.match_analysis}

---
## 🌐 联网检索原始事实记录（Google Search）
{search_text}
"""
    ai_report = db.query(MatchAIReport).filter(MatchAIReport.match_id == match.id).first()
    if ai_report:
        ai_report.content = report_md
    else:
        ai_report = MatchAIReport(match_id=match.id, content=report_md)
        db.add(ai_report)
        
    db.commit()
    print("  [DB] 成功写入/更新 teams 与 match_ai_reports 数据库记录。")
    
    # ─── 物理联动：重新计算预测 ───
    print("  [Prediction] 正在调用预测融合引擎重新预测并校准赔率/比分...")
    engine = PredictionEngine(db_session=db)
    ctx = build_context_from_match(match)
    res = engine.predict(ctx)
    
    # 清除旧的预测条目
    db.query(Prediction).filter(Prediction.match_id == match.id).delete()
    # 写入全新校准过的预测
    for p in res.to_db_payload():
        db.add(Prediction(
            match_id=match.id,
            play_type=p["play_type"],
            probabilities=p["probabilities"],
            model_version=res.model_version,
            confidence=res.confidence
        ))
        
    db.commit()
    print("  [Prediction] 已完成 SPF/SCORE/GOALS 等玩法概率的贝叶斯重校准与持久化。")
    return True

# ─── 主入口 ───
def main():
    parser = argparse.ArgumentParser(description="独立的赛前新闻舆情检索与情报量化 Agent")
    parser.add_argument("--match-code", type=str, help="指定要更新的单场赛事代码 (例如 WC2026-H1)")
    parser.add_argument("--days", type=int, help="扫描未来 N 天内开赛的赛事")
    parser.add_argument("--all-scheduled", action="store_true", help="全量扫描所有非 FINISHED 状态的比赛")
    parser.add_argument("--dry-run", action="store_true", help="只进行检索和模型量化，不修改数据库和预测")
    args = parser.parse_args()
    
    if not (args.match_code or args.days or args.all_scheduled):
        parser.print_help()
        sys.exit(1)
        
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        print("❌ 错误：环境变量中未检测到 GEMINI_API_KEY，请检查 backend/.env 文件配置。")
        sys.exit(1)
        
    client = genai.Client(api_key=api_key)
    db = SessionLocal()
    
    try:
        query = db.query(Match).filter(Match.status != MatchStatus.FINISHED)
        
        # 参数逻辑过滤
        if args.match_code:
            query = query.filter(Match.match_code == args.match_code)
        elif args.days:
            now = datetime.now(timezone.utc)
            future_bound = now + timedelta(days=args.days)
            query = query.filter(Match.kickoff_at >= now, Match.kickoff_at <= future_bound)
            
        matches = query.all()
        if not matches:
            print("⚠️ 未找到匹配的待预测比赛场次。")
            return
            
        print(f"🎯 检索完毕：共找到 {len(matches)} 场比赛符合舆情注入条件。")
        
        success = 0
        for m in matches:
            # 基础数据容错校验
            if not m.home_team or not m.away_team:
                print(f"  [Skip] 比赛 {m.match_code} (ID: {m.id}) 缺少对应球队数据，已跳过。")
                continue
            try:
                if process_match_morale(db, m, client, dry_run=args.dry_run):
                    success += 1
            except Exception as e:
                print(f"❌ 比赛 {m.match_code} 执行异常，已捕获: {e}")
                db.rollback()
                continue
                
        print(f"\n🎉 舆情更新任务执行完毕！成功处理: {success}/{len(matches)} 场焦点赛事。")
        
    finally:
        db.close()

if __name__ == "__main__":
    main()
