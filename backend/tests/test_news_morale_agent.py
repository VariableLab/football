# -*- coding: utf-8 -*-
"""
test_news_morale_agent.py - 针对新闻舆情检索与情报量化 Agent 的单元测试

主要测试点：
1. 双阶段模型联网与结构化数据提取的集成流程。
2. 数据库落库操作（更新 teams, match_ai_reports）。
3. 联动预测重算（PredictionEngine 预测更新写回 predictions）。
4. 针对 API 503 UNAVAILABLE 错误的指数退避重试逻辑。
"""

import sys
import os
from unittest.mock import patch, MagicMock
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# 确保 backend 根目录在 path 中
_current_dir = os.path.dirname(os.path.abspath(__file__))
_backend_root = os.path.dirname(_current_dir)
if _backend_root not in sys.path:
    sys.path.insert(0, _backend_root)

# 确保导入相关的 module
from database.models import Base, Match, Team, MatchAIReport, Prediction, MatchStatus
from scripts.news_morale_agent import (
    process_match_morale,
    generate_content_with_retry,
    MatchMoraleReport,
    TeamMorale
)

# ─── 测试用内存 SQLite 数据库 Fixture ───
@pytest.fixture
def db_session():
    """创建一个干净的、与生产数据完全隔离的内存数据库"""
    engine = create_engine("sqlite:///:memory:")
    TestingSessionLocal = sessionmaker(bind=engine)
    
    # 创建表结构
    Base.metadata.create_all(bind=engine)
    
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)

# ─── Mock 数据生成器 ───
def insert_test_match_data(db):
    """向测试数据库插入主客队和一场待预测比赛"""
    home = Team(
        id=1,
        name="沙特",
        name_en="Saudi Arabia",
        code="KSA",
        elo=1600,
        form_factor=1.0,
        key_injuries=""
    )
    away = Team(
        id=2,
        name="乌拉圭",
        name_en="Uruguay",
        code="URU",
        elo=1800,
        form_factor=1.0,
        key_injuries=""
    )
    match = Match(
        id=101,
        match_code="WC2026-H1",
        home_team_id=1,
        away_team_id=2,
        status=MatchStatus.SCHEDULED,
        venue_type="neutral"
    )
    db.add(home)
    db.add(away)
    db.add(match)
    db.commit()
    return match

# ─── 核心功能单元测试 ───
class TestNewsMoraleAgent:
    
    @patch("scripts.news_morale_agent.PredictionEngine")
    def test_process_match_morale_success_flow(self, mock_engine_class, db_session):
        """测试正常成功的流程：检索、量化提取、落库与预测引擎调用"""
        
        # 1. 模拟 PredictionEngine 预测返回值
        mock_engine = MagicMock()
        mock_prediction_res = MagicMock()
        mock_prediction_res.model_version = "v2.0-test"
        mock_prediction_res.confidence = "high"
        mock_prediction_res.to_db_payload.return_value = [
            {"play_type": "SPF", "probabilities": {"home": 0.4, "draw": 0.3, "away": 0.3}},
            {"play_type": "SCORE", "probabilities": {"1-0": 0.1, "0-0": 0.1}}
        ]
        mock_engine.predict.return_value = mock_prediction_res
        mock_engine_class.return_value = mock_engine
        
        # 2. 插入假比赛数据
        match = insert_test_match_data(db_session)
        
        # 3. 模拟 Gemini API 客户端
        mock_client = MagicMock()
        
        # 阶段 1 返回的对象
        mock_resp_stage1 = MagicMock()
        mock_resp_stage1.text = "阶段1 联网检索到的新闻正文：沙特队状态出色，卡努轻伤。乌拉圭队阿劳霍受伤缺席，行程受阻。"
        
        # 阶段 2 返回的对象（带 parsed Pydantic 结构）
        mock_resp_stage2 = MagicMock()
        mock_report = MatchMoraleReport(
            home=TeamMorale(
                team_name="沙特",
                morale_score=0.4,
                key_injuries="穆罕默德-卡努(伤)",
                retrieved_sources="利雅得新月官方消息",
                rationale="虽然中场卡努有伤，但整体士气高涨，备战充分。"
            ),
            away=TeamMorale(
                team_name="乌拉圭",
                morale_score=-0.8,
                key_injuries="罗纳德·阿劳霍(伤),德阿拉斯卡埃塔(伤)",
                retrieved_sources="乌拉圭足协公告",
                rationale="核心后卫阿劳霍受伤，球队美加墨之行航班严重受阻，士气受创。"
            ),
            match_analysis="乌拉圭实力占优但伤病与备战极度受挫，沙特防守纪律性强，有爆冷机会。"
        )
        mock_resp_stage2.parsed = mock_report
        
        # 拦截两次 generate_content 调用
        mock_client.models.generate_content.side_effect = [mock_resp_stage1, mock_resp_stage2]
        
        # 4. 执行业务逻辑
        result = process_match_morale(db_session, match, mock_client, dry_run=False)
        
        # 5. 断言校验
        assert result is True
        
        # 验证球队表字段是否更新
        updated_home = db_session.query(Team).filter(Team.id == 1).first()
        updated_away = db_session.query(Team).filter(Team.id == 2).first()
        
        # 沙特士气 0.4 -> form_factor = 1.0 + 0.4 = 1.4
        assert abs(updated_home.form_factor - 1.4) < 1e-5
        assert updated_home.key_injuries == "穆罕默德-卡努(伤)"
        
        # 乌拉圭士气 -0.8 -> form_factor = 1.0 - 0.8 = 0.2 -> 被 min/max 限幅在 0.5 
        assert abs(updated_away.form_factor - 0.5) < 1e-5
        assert updated_away.key_injuries == "罗纳德·阿劳霍(伤),德阿拉斯卡埃塔(伤)"
        
        # 验证 AI 赛前报告表是否成功写入
        report_record = db_session.query(MatchAIReport).filter(MatchAIReport.match_id == match.id).first()
        assert report_record is not None
        assert "AI 精算师赛前情报量化报告" in report_record.content
        assert "罗纳德·阿劳霍(伤)" in report_record.content
        assert "1.40" in report_record.content  # 报告里有主队因子
        
        # 验证联动预测数据是否写回
        preds = db_session.query(Prediction).filter(Prediction.match_id == match.id).all()
        assert len(preds) == 2
        play_types = [p.play_type for p in preds]
        assert "SPF" in play_types
        assert "SCORE" in play_types
        assert preds[0].model_version == "v2.0-test"

    def test_generate_content_with_retry_error_and_success(self):
        """测试退避重试逻辑：先抛出异常（如 503 忙碌），最终重试成功"""
        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.text = "成功返回"
        
        # 模拟前三次调用全部报错 (模拟 API 503 异常)，第四次成功
        mock_client.models.generate_content.side_effect = [
            Exception("503 Server Busy"),
            Exception("503 Server Busy"),
            Exception("503 Server Busy"),
            mock_resp
        ]
        
        # 运行重试（使用极小的初始退避时间 0.01s 避免单元测试过慢）
        res = generate_content_with_retry(
            client=mock_client,
            model="gemini-2.5-flash",
            contents="test",
            config=None,
            max_retries=5,
            initial_backoff=0.01
        )
        
        assert res.text == "成功返回"
        assert mock_client.models.generate_content.call_count == 4

    def test_generate_content_with_retry_all_fail(self):
        """测试退避重试逻辑：全部 5 次均报错，最终抛出异常"""
        mock_client = MagicMock()
        mock_client.models.generate_content.side_effect = Exception("503 Server Busy")
        
        with pytest.raises(Exception) as excinfo:
            generate_content_with_retry(
                client=mock_client,
                model="gemini-2.5-flash",
                contents="test",
                config=None,
                max_retries=5,
                initial_backoff=0.01
            )
            
        assert "503 Server Busy" in str(excinfo.value)
        assert mock_client.models.generate_content.call_count == 5
