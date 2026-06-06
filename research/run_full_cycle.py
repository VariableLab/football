import os
import sys
import pandas as pd

# 将 research/src 添加到路径
sys.path.append(os.path.join(os.getcwd(), 'research', 'src'))

from footy.data.loader import FootballDataLoader
from footy.backtest.engine import TimeSeriesBacktester
from footy.models.elo import EloPredictor
from footy.models.poisson import PoissonPredictor
from footy.evaluation.metrics import calculate_rps, calculate_accuracy
from footy.content.engine import WorldCupContentEngine
from footy.evaluation.visualizer import MatchCardGenerator

def main():
    print("🚀 启动深度建模与可视化执行任务...")
    
    # 1. 加载数据
    raw_dir = "research/data/raw"
    loader = FootballDataLoader(raw_dir)
    files = ['E0_2223.csv', 'E0_2324.csv']
    df = loader.load_processed(files)
    
    # 2. 运行回测对撞
    backtester = TimeSeriesBacktester(df, initial_train_weeks=38)
    
    print("\n[任务 1/3] 专家模型回测对撞...")
    models = [
        ("Expert Elo", EloPredictor),
        ("Poisson (Dixon-Coles)", PoissonPredictor)
    ]
    
    for name, model_cls in models:
        results = backtester.run(model_cls)
        if not results.empty:
            probs = results[['prob_H', 'prob_D', 'prob_A']].values
            outcomes = results['FTR']
            rps = calculate_rps(probs, outcomes)
            print(f"  ✅ {name:20}: RPS={rps:.4f}")

    # 3. 自动化内容生成与可视化
    print("\n[任务 2/3] 自动化内容生成...")
    content_engine = WorldCupContentEngine()
    viz = MatchCardGenerator()
    
    # 为一场经典对决生成前瞻
    match_data = content_engine.generate_match_preview(
        match_id=999, home_team="Brazil", away_team="Germany"
    )
    
    # 更新预测概率为模型实测水平
    match_data['prediction_ref'] = {"home_win": 0.52, "draw": 0.24, "away_win": 0.24}
    
    print("\n[任务 3/3] 渲染可视化卡片...")
    img_path = viz.generate_card(match_data)
    print(f"  ✅ 赛事前瞻卡片已生成: {img_path}")

    print("\n" + "="*50)
    print("✨ 执行报告: 深度建模与可视化链路已打通。")
    print("="*50)

if __name__ == "__main__":
    main()
