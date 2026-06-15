# -*- coding: utf-8 -*-
"""
test_exchange_monitor.py - 针对必发交易所资金监控与 AI 联动预警的单元测试

测试覆盖：
1. 必发网页抓取 HTML 解析。
2. 队名内存模糊实体配对算法。
3. 赔率隐含概率反推（去 margin）。
4. 偏离度 Bias 偏离超限判定与异常警报触发。
5. 自动联动 Agent 舆情重估 + 联动 PredictionEngine 重新预测。
6. Telegram 推送通知。
"""

import sys
import os
from datetime import datetime, timedelta, timezone
from unittest.mock import patch, MagicMock
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# 确保 backend 根目录在 path 中
_current_dir = os.path.dirname(os.path.abspath(__file__))
_backend_root = os.path.dirname(_current_dir)
if _backend_root not in sys.path:
    sys.path.insert(0, _backend_root)

from database.models import Base, Match, Team, BettingExchangeVolume, MatchAIReport, Prediction, MatchStatus
from ingestion.bf_volume_scraper import BFVolumeScraper
from monitor.exchange_anomaly_daemon import run_monitor_cycle, compute_implied_probabilities

# ─── 数据库隔离 Fixture ───
@pytest.fixture
def db_session():
    """测试用内存数据库"""
    engine = create_engine("sqlite:///:memory:")
    TestingSessionLocal = sessionmaker(bind=engine)
    Base.metadata.create_all(bind=engine)
    
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)

# ─── 测试数据构造 ───
def setup_match_with_odds(db):
    """插入意大利 vs 韩国比赛，带有初始赔率与合法的开赛时间"""
    home = Team(
        id=10,
        name="意大利",
        name_en="Italy",
        code="ITA",
        elo=1750,
        form_factor=1.0
    )
    away = Team(
        id=11,
        name="韩国",
        name_en="Korea Republic",
        code="KOR",
        elo=1620,
        form_factor=1.0
    )
    match = Match(
        id=88,
        match_code="WC2026-H1",
        home_team_id=10,
        away_team_id=11,
        status=MatchStatus.SCHEDULED,
        # 赔率: 胜1.5, 平4.0, 负6.5 (主队有巨大优势)
        odds_home=1.5,
        odds_draw=4.0,
        odds_away=6.5,
        venue_type="neutral",
        # 💡 填充 kickoff_at 确保不被时间窗口过滤 (未来 12 小时)
        kickoff_at=datetime.now(timezone.utc) + timedelta(hours=12)
    )
    db.add(home)
    db.add(away)
    db.add(match)
    db.commit()
    return match

# ─── 单元测试 ───
class TestExchangeMonitor:
    
    @patch("ingestion.bf_volume_scraper.httpx.get")
    def test_scraper_html_parsing_and_alignment(self, mock_get, db_session):
        """测试网页抓取、HTML 解析与模糊队名配对"""
        match = setup_match_with_odds(db_session)
        
        # 模拟球探网必发页面的表格 HTML (第一行为表头，第二行为意大利 vs 韩国)
        mock_html = """
        <html>
            <body>
                <table id="table_live">
                    <tr class="header">
                        <th>序号</th><th>主队</th><th>平局</th><th>客队</th><th>总交易</th><th>主%</th><th>平%</th><th>客%</th>
                    </tr>
                    <tr>
                        <td>1</td>
                        <td>意大利</td>
                        <td>vs</td>
                        <td>韩国</td>
                        <td>5,000,000</td>
                        <td>15%</td>
                        <td>10%</td>
                        <td>75%</td>
                    </tr>
                </table>
            </body>
        </html>
        """
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = mock_html
        mock_get.return_value = mock_resp
        
        scraper = BFVolumeScraper(db_session)
        aligned_count = scraper.scrape_and_align(days_ahead=3)
        
        # 断言匹配成功 1 场
        assert aligned_count == 1
        
        # 验证数据库中是否生成了对应的资金分布记录
        vol_record = db_session.query(BettingExchangeVolume).filter(
            BettingExchangeVolume.match_id == match.id
        ).first()
        
        assert vol_record is not None
        assert vol_record.total_volume == 5000000.0
        assert abs(vol_record.home_ratio - 0.15) < 1e-5
        assert abs(vol_record.away_ratio - 0.75) < 1e-5

    def test_compute_implied_probabilities(self, db_session):
        """测试赔率隐含概率反推 (去 margin) 算法的精度"""
        match = setup_match_with_odds(db_session)
        probs = compute_implied_probabilities(match)
        
        assert probs is not None
        # 1/1.5 + 1/4.0 + 1/6.5 = 0.6666 + 0.25 + 0.1538 = 1.0705
        # 胜: (1/1.5)/1.0705 = 0.6227
        # 平: (1/4.0)/1.0705 = 0.2335
        # 负: (1/6.5)/1.0705 = 0.1437
        assert abs(probs["home"] - 0.6227) < 1e-3
        assert abs(probs["draw"] - 0.2335) < 1e-3
        assert abs(probs["away"] - 0.1437) < 1e-3
        assert abs(sum(probs.values()) - 1.0) < 1e-5

    @patch("monitor.exchange_anomaly_daemon.send_telegram_markdown_message")
    @patch("monitor.exchange_anomaly_daemon.process_match_morale")
    @patch("monitor.exchange_anomaly_daemon.BFVolumeScraper")
    def test_anomaly_detection_and_alarm_trigger(self, mock_scraper_class, mock_process_morale, mock_send_tg, db_session):
        """测试当资金偏离度超标时，成功触发 AI 检索与 Telegram 报警"""
        match = setup_match_with_odds(db_session)
        
        # 1. 模拟必发抓取器已经落库的数据：主胜实际成交只有 15%，而暗含赔率胜率是 62%
        # 产生极大的偏离度 Bias = |0.15 - 0.62| = 0.47 > 0.25 阈值
        vol = BettingExchangeVolume(
            match_id=match.id,
            total_volume=3500000.0,
            home_ratio=0.15,
            draw_ratio=0.10,
            away_ratio=0.75
        )
        db_session.add(vol)
        db_session.commit()
        
        # 2. 模拟 Ingestion 刷新已完成
        mock_scraper = MagicMock()
        mock_scraper.scrape_and_align.return_value = 1
        mock_scraper_class.return_value = mock_scraper
        
        # 3. 模拟 process_match_morale 执行成功
        mock_process_morale.return_value = True
        
        # 4. 插入假预测和假 AI 报告，用于组装推送 Markdown 
        prediction = Prediction(
            match_id=match.id,
            play_type="SPF",
            probabilities={"home": 0.16, "draw": 0.12, "away": 0.72},
            model_version="v2.0-test"
        )
        ai_report = MatchAIReport(
            match_id=match.id,
            content="测试 AI 报告内容：韩国队逆转后士气暴涨，意大利面临重大伤病潮。"
        )
        db_session.add(prediction)
        db_session.add(ai_report)
        db_session.commit()
        
        # 5. 执行监控轮巡
        mock_client = MagicMock()
        run_monitor_cycle(db_session, mock_client, dry_run=False)
        
        # 6. 断言校验
        # 验证是否成功调用了舆情检索 Agent 的联动函数
        mock_process_morale.assert_called_once_with(db_session, match, mock_client, dry_run=False)
        
        # 验证是否调用了 Telegram 消息推送
        mock_send_tg.assert_called_once()
        sent_md = mock_send_tg.call_args[0][0]
        
        assert "必发交易所资金异常警报" in sent_md
        assert "WC2026-H1" in sent_md
        assert "测试 AI 报告内容" in sent_md
        assert "3,500,000" in sent_md
        assert "韩国" in sent_md
