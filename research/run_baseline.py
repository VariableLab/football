import os
import sys

# 将 research/src 添加到路径
sys.path.append(os.path.join(os.getcwd(), 'research', 'src'))

from footy.data.loader import FootballDataLoader
from footy.backtest.engine import TimeSeriesBacktester
from footy.models.baseline import HistoricalFrequencyBaseline, HomeWinBaseline
from footy.evaluation.metrics import calculate_rps, calculate_accuracy

def main():
    print("⚽ Football Prediction Research - Baseline Experiment")
    
    # 1. 设置数据
    raw_dir = "research/data/raw"
    loader = FootballDataLoader(raw_dir)
    
    # 下载英超 22/23 和 23/24 赛季
    print("📥 Loading data...")
    # 注意: football-data.co.uk 的代码是 E0
    # 由于环境可能无法访问外网，如果下载失败，请手动将 CSV 放入 research/data/raw/
    try:
        loader.download_season('E0', '2223')
        loader.download_season('E0', '2324')
    except Exception as e:
        print(f"⚠️ Download failed: {e}. Please ensure data is in {raw_dir}")

    files = ['E0_2223.csv', 'E0_2324.csv']
    # 检查文件是否存在
    files = [f for f in files if os.path.exists(os.path.join(raw_dir, f))]
    if not files:
        print("❌ No data files found. Exiting.")
        return

    df = loader.load_processed(files)
    print(f"✅ Loaded {len(df)} matches.")

    # 2. 运行回测
    backtester = TimeSeriesBacktester(df, initial_train_weeks=38) # 用一整个赛季的数据作为初始训练
    
    print("\n运行基准模型对比...")
    
    baselines = [
        ("Home Win Only", HomeWinBaseline),
        ("Historical Freq", HistoricalFrequencyBaseline)
    ]
    
    for name, model_cls in baselines:
        print(f"▶️ Testing: {name}...")
        results = backtester.run(model_cls)
        
        if not results.empty:
            probs = results[['prob_H', 'prob_D', 'prob_A']].values
            outcomes = results['FTR']
            
            acc = calculate_accuracy(probs, outcomes)
            rps = calculate_rps(probs, outcomes)
            
            print(f"   📊 Results: Accuracy={acc:.1%}, RPS={rps:.4f}")
        else:
            print("   ⚠️ No results (check date range).")

if __name__ == "__main__":
    main()
