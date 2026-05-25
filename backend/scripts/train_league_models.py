import sys
import os
import time
import logging

# Ensure we can import from backend
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from fusion.fusion_trainer import FusionTrainer
from config import get_settings

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger("league_trainer")

def train_all_leagues():
    # 目标联赛清单
    leagues = ["EPL", "LaLiga", "SerieA", "Bundesliga", "Ligue1", "JLeague"]
    
    trainer = FusionTrainer(limit=None)
    
    print("🚀 开始训练分联赛垂直模型...")
    t0 = time.time()
    
    for league in leagues:
        print(f"\n[任务] 正在训练 {league} 专属模型...")
        try:
            w = trainer.train_league(league)
            if w:
                print(f"  ✅ 成功: Accuracy={w.accuracy:.4f}, Samples={w.sample_count}")
                path = w.save()
                print(f"  💾 已保存: {path}")
            else:
                print(f"  ⚠️ 失败: 样本不足或训练未收敛")
        except Exception as e:
            print(f"  ❌ 错误: {e}")
            
    print(f"\n🎉 所有模型训练完成！总耗时: {(time.time()-t0)/60:.1f} 分钟")

if __name__ == "__main__":
    # 需要设置环境变量
    train_all_leagues()
