import os
import sys
import pandas as pd

# 将 research/src 添加到路径
sys.path.append(os.path.join(os.getcwd(), 'research', 'src'))

from footy.data.loader import FootballDataLoader
from footy.models.poisson import PoissonPredictor

def export_best_weights():
    print("🎓 Training best Poisson model for F1 Hotfix...")
    
    # 1. 加载全量英超历史数据用于训练
    raw_dir = "research/data/raw"
    loader = FootballDataLoader(raw_dir)
    files = ['E0_2223.csv', 'E0_2324.csv']
    df = loader.load_processed(files)
    
    # 2. 训练专家模型
    model = PoissonPredictor()
    model.fit(df, df['FTR'])
    
    # 3. 导出参数
    output_path = "research/data/processed/poisson_expert_weights.json"
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    model.save_params(output_path)
    
    print(f"✅ Exported weights for {len(model.team_params)} teams.")

if __name__ == "__main__":
    export_best_weights()
