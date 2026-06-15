import os
import sys
import pytest

# 添加 backend 目录到 path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

# 导入 prediction_engine 中的核心类
from core.prediction_engine import PredictionEngine, create_mock_context

def test_score_and_spf_probability_coherence():
    """验证重构后的贝叶斯校准逻辑是否保证比分和胜平负概率完全自洽"""
    # 1. 模拟一场强队对阵弱队的比赛
    ctx = create_mock_context(
        match_id=1,
        home_elo=1850,
        away_elo=1550,
        odds_home=1.45,
        odds_draw=4.20,
        odds_away=6.50
    )
    
    # 实例化引擎运行预测
    engine = PredictionEngine(db_session=None)
    res = engine.predict(ctx)
    
    # 获取输出
    spf = res.spf
    score = res.score
    goals = res.goals
    half = res.half
    
    print("\n--- Calibration Check (Home Strong) ---")
    print(f"SPF Probabilities: {spf}")
    print(f"Top Scores: {sorted(score.items(), key=lambda x: -x[1])[:5]}")
    
    # 2. 分类比分，验证概率和
    sum_home = 0.0
    sum_draw = 0.0
    sum_away = 0.0
    
    for key, val in score.items():
        h, a = map(int, key.split(':'))
        if h > a:
            sum_home += val
        elif h < a:
            sum_away += val
        else:
            sum_draw += val
            
    # 因为有过滤和舍入误差（只保留概率大等于 0.5% 的比分），我们验证它们的偏差应该极小（如在 1% 范围内）
    assert abs(sum_home - spf["home"]) < 0.02, f"Home score sum {sum_home} devates from SPF home {spf['home']}"
    assert abs(sum_draw - spf["draw"]) < 0.02, f"Draw score sum {sum_draw} devates from SPF draw {spf['draw']}"
    assert abs(sum_away - spf["away"]) < 0.02, f"Away score sum {sum_away} devates from SPF away {spf['away']}"
    
    # 3. 验证总进球概率和是否接近 1.0
    sum_goals = sum(goals.values())
    assert abs(sum_goals - 1.0) < 0.02, f"Goals probability sum is {sum_goals}, expected close to 1.0"
    
    # 4. 验证半全场概率和是否接近 1.0
    sum_half = sum(half.values())
    assert abs(sum_half - 1.0) < 0.02, f"Half probability sum is {sum_half}, expected close to 1.0"


def test_score_coherence_on_degraded_fallback():
    """验证在数据缺失降级状态下，比分校准是否仍然合理自洽，排除千篇一律的逻辑冲突"""
    # 模拟数据缺失的 MatchContext (Elo 回退到 1500，fifa_rank 100)
    ctx = create_mock_context(
        match_id=2,
        home_elo=1500,
        away_elo=1500,
        odds_home=2.00,
        odds_draw=3.40,
        odds_away=3.80
    )
    # 标记为 degraded (通过强制覆盖 team name 和 values 使其匹配不上专家 weights)
    ctx.home_team.name = "UnknownHome"
    ctx.home_team.name_en = "UnknownHome"
    ctx.away_team.name = "UnknownAway"
    ctx.away_team.name_en = "UnknownAway"
    
    engine = PredictionEngine(db_session=None)
    res = engine.predict(ctx)
    
    spf = res.spf
    score = res.score
    
    print("\n--- Calibration Check (Degraded Fallback) ---")
    print(f"SPF: {spf}")
    print(f"Top Scores: {sorted(score.items(), key=lambda x: -x[1])[:5]}")
    
    sum_home = sum(val for key, val in score.items() if int(key.split(':')[0]) > int(key.split(':')[1]))
    sum_draw = sum(val for key, val in score.items() if int(key.split(':')[0]) == int(key.split(':')[1]))
    sum_away = sum(val for key, val in score.items() if int(key.split(':')[0]) < int(key.split(':')[1]))
    
    assert abs(sum_home - spf["home"]) < 0.02
    assert abs(sum_draw - spf["draw"]) < 0.02
    assert abs(sum_away - spf["away"]) < 0.02
