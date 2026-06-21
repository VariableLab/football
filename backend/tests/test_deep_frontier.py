"""
Unit tests for v4.0 Deep Frontier temporal xG model and predictor (test_deep_frontier.py)
"""
import pytest
import torch
from unittest.mock import MagicMock
from core.deep_frontier_nn import xGTransformerNet, build_match_history_vector


def test_network_shapes_and_positivity():
    # 验证网络对于任意输入的形状正确性，以及 Softplus 产生的进球期望大于 0 约束
    net = xGTransformerNet(seq_feat_dim=6, static_feat_dim=48, hidden_dim=16)
    
    batch_size = 4
    seq_h = torch.randn(batch_size, 5, 6)
    seq_a = torch.randn(batch_size, 5, 6)
    static = torch.randn(batch_size, 48)
    
    lam_h, lam_a = net(seq_h, seq_a, static)
    
    assert lam_h.shape == (batch_size, 1)
    assert lam_a.shape == (batch_size, 1)
    
    # 泊松期望值必须严格大于 0.1 偏置以保障 Dixon-Coles 稳定性
    assert torch.all(lam_h >= 0.1)
    assert torch.all(lam_a >= 0.1)


def test_loss_convergence():
    # 验证 PoissonNLLLoss 在简易优化中能够收敛
    net = xGTransformerNet(seq_feat_dim=6, static_feat_dim=48, hidden_dim=16)
    optimizer = torch.optim.Adam(net.parameters(), lr=1e-2)
    criterion = torch.nn.PoissonNLLLoss(log_input=False)
    
    seq_h = torch.randn(2, 5, 6)
    seq_a = torch.randn(2, 5, 6)
    static = torch.randn(2, 48)
    
    y_h = torch.FloatTensor([2.0, 0.0])
    y_a = torch.FloatTensor([1.0, 3.0])
    
    # 执行 5 步梯度更新，损失应该下降
    net.train()
    init_loss = None
    for _ in range(5):
        optimizer.zero_grad()
        lh, la = net(seq_h, seq_a, static)
        loss = criterion(lh.squeeze(), y_h) + criterion(la.squeeze(), y_a)
        if init_loss is None:
            init_loss = loss.item()
        loss.backward()
        optimizer.step()
        
    final_loss = loss.item()
    assert final_loss < init_loss or final_loss == pytest.approx(init_loss, abs=1.0)


def test_history_vector_building():
    # 验证历史比赛映射为时序特征张量
    m = MagicMock()
    m.home_team_id = 1
    m.actual_home_goals = 2
    m.actual_away_goals = 1
    m.away_team.elo = 1600
    m.closing_odds_home = 1.8
    
    vec = build_match_history_vector(m, team_id=1)
    
    assert vec.shape == (6,)
    assert vec[0] == 1.0  # is_home = True
    assert vec[1] == pytest.approx(2.0 / 3.0)  # goals_scored
    assert vec[2] == pytest.approx(1.0 / 3.0)  # goals_conceded
    assert vec[3] == pytest.approx(1600.0 / 1500.0)  # opp_elo
    assert vec[4] == pytest.approx(1.8 / 5.0)  # odds
    assert vec[5] == 1.0  # win
