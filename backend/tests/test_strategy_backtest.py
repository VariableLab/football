import pytest
from strategy.ev_maximizing_strategy import EVMaximizingStrategy

def test_ev_strategy_positive_roi_baseline():
    """
    策略回测门禁测试
    验证 EVMaximizingStrategy 在有利赔率下是否能产生合理的投注建议。
    """
    # 修改为爆冷高赔正期望项 (odds >= 2.8)
    # 比如真实概率 主 0.40, 平 0.30, 客 0.30
    # 赔率 主 3.0, 平 3.0, 客 2.5
    predictions = [
        {"play_type": "SPF", "probabilities": {"home": 0.40, "draw": 0.30, "away": 0.30}},
    ]
    
    strategy = EVMaximizingStrategy(
        match_predictions=predictions,
        odds_home=3.0,
        odds_draw=3.0,
        odds_away=2.5,
        collapse_prob=0.05,
        home_team_name="Home",
        away_team_name="Away"
    )
    
    # 期望产生包含正 EV (home) 的推荐
    recommendations = strategy.generate(min_ev=0.05)
    
    assert len(recommendations) > 0, "有利赔率下必须产生投注建议"
    
    best_portfolio = recommendations[0]
    assert best_portfolio.expected_roi > 0.05, "最优投资组合的期望价值必须达到最小基线"
    assert "home" in str(best_portfolio.legs), "投资组合中必须包含最有价值的主胜选项"
    
def test_ev_strategy_rejects_negative_ev():
    """
    验证策略会自动拒绝没有正向 EV 的比赛，不会盲目推荐。
    """
    # 模型概率完全等于赔率隐含概率 (且包含了抽水)
    # 赔率 2.0 3.0 4.0 -> 隐含 50%, 33.3%, 25% (和=108.3%)
    # 真实概率 主 0.45, 平 0.30, 客 0.25
    predictions = [
        {"play_type": "SPF", "probabilities": {"home": 0.45, "draw": 0.30, "away": 0.25}},
    ]
    
    strategy = EVMaximizingStrategy(
        match_predictions=predictions,
        odds_home=2.0,
        odds_draw=3.0,
        odds_away=4.0,
        collapse_prob=0.05,
        home_team_name="Home",
        away_team_name="Away"
    )
    
    recommendations = strategy.generate(min_ev=0.01)
    
    assert len(recommendations) == 0, "没有足够优势时不应提供推荐"
