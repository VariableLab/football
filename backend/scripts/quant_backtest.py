"""
Quantitative Backtest Engine — 多维度盈亏与风险评估

核心指标:
1. ROI (投资回报率)
2. Max Drawdown (最大回撤)
3. Sharpe Ratio (风险调整后收益)
4. Win Rate (胜率)
5. Profit/Loss (累积盈亏曲线)
"""
import numpy as np
import pandas as pd
from typing import List, Dict, Any, Optional
from datetime import datetime

class QuantBacktestEngine:
    def __init__(self, initial_capital: float = 10000.0):
        self.initial_capital = initial_capital
        self.reset()

    def reset(self):
        self.capital = self.initial_capital
        self.equity_curve = [self.initial_capital]
        self.history = [] # 记录每场博弈详情

    def run(self, match_data: List[Dict[str, Any]], stake_rule: str = 'fixed', unit: float = 100.0):
        """
        运行回测
        match_data: [{
            'prob': 0.6, 'odds': 2.1, 'actual': 'home', 'selection': 'home', 'date': '...'
        }]
        stake_rule: 'fixed' (固定单位) or 'kelly' (凯利公式)
        """
        self.reset()
        
        for m in match_data:
            prob = m['prob']
            odds = m['odds']
            actual = m['actual']
            selection = m['selection']
            
            # 计算投注额
            if stake_rule == 'fixed':
                stake = unit
            elif stake_rule == 'kelly':
                # 1/4 Kelly 建议
                k = (prob * odds - 1.0) / (odds - 1.0) if odds > 1 else 0
                stake = max(0, self.capital * k * 0.25)
            else:
                stake = unit

            if stake > self.capital: # 爆仓检查
                stake = self.capital
            
            if stake <= 0: continue

            # 计算损益
            win = (selection == actual)
            if win:
                pnl = stake * (odds - 1)
            else:
                pnl = -stake
            
            self.capital += pnl
            self.equity_curve.append(self.capital)
            
            self.history.append({
                'date': m.get('date'),
                'match': m.get('match'),
                'stake': stake,
                'pnl': pnl,
                'balance': self.capital,
                'win': win
            })

    def get_metrics(self) -> Dict[str, Any]:
        """计算核心量化指标"""
        if not self.history:
            return {}

        df = pd.DataFrame(self.history)
        returns = pd.Series(self.equity_curve).pct_change().dropna()
        
        # 1. 累计收益
        total_pnl = self.capital - self.initial_capital
        roi = total_pnl / self.initial_capital
        
        # 2. 胜率
        win_rate = df['win'].mean()
        
        # 3. 最大回撤
        peak = pd.Series(self.equity_curve).expanding().max()
        drawdown = (pd.Series(self.equity_curve) - peak) / peak
        max_drawdown = drawdown.min()
        
        # 4. 夏普比率 (假设无风险利率 0.02, 简单折算)
        avg_ret = returns.mean()
        std_ret = returns.std()
        sharpe = (avg_ret - 0.0001) / std_ret if std_ret > 0 else 0
        
        return {
            "total_pnl": round(total_pnl, 2),
            "roi": round(roi, 4),
            "win_rate": round(win_rate, 4),
            "max_drawdown": round(max_drawdown, 4),
            "sharpe_ratio": round(sharpe, 4),
            "sample_size": len(df),
            "equity_curve": self.equity_curve
        }
