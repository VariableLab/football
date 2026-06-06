import os
import sys
import pandas as pd

# 将 research/src 添加到路径
sys.path.append(os.path.join(os.getcwd(), 'research', 'src'))

from footy.data.loader import FootballDataLoader
from footy.data.kaggle_international import KaggleInternationalLoader
from footy.models.elo import EloPredictor

def export_best_elo():
    print("🎓 Training best Elo model for F1 Upgrade...")
    
    # 1. 加载国际赛历史数据（更全的 Elo 基准）
    raw_dir = "research/data/raw"
    kaggle_loader = KaggleInternationalLoader(raw_dir)
    df_intl = kaggle_loader.load_results()
    
    # 预处理国际赛数据以适配 EloPredictor
    # 国际赛列名: date, home_team, away_team, home_score, away_score
    df_intl = df_intl.rename(columns={
        'home_team': 'HomeTeam',
        'away_team': 'AwayTeam'
    })
    def get_ftr(row):
        if row['home_score'] > row['away_score']: return 'H'
        if row['home_score'] < row['away_score']: return 'A'
        return 'D'
    
    # 过滤掉未来的比赛（score 为 NaN 的）
    df_intl = df_intl.dropna(subset=['home_score', 'away_score'])
    df_intl['FTR'] = df_intl.apply(get_ftr, axis=1)
    
    # 2. 训练专家模型
    model = EloPredictor(k_factor=20, home_advantage=50) # 国际赛常用的参数
    model.fit(df_intl, df_intl['FTR'])
    
    # 3. 导出参数
    output_path = "research/data/processed/elo_expert_weights.json"
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    model.save_params(output_path)
    
    print(f"✅ Exported Elo ratings for {len(model.elo_ratings)} teams.")

if __name__ == "__main__":
    export_best_elo()
