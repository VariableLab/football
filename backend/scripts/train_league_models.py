import sys
import os
import time
import logging

# Ensure we can import from backend

from fusion.fusion_trainer import FusionTrainer

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger("league_trainer")

def train_all_leagues():
    # 目标联赛清单
    tier_a_leagues = ["EPL", "LaLiga", "SerieA", "Bundesliga", "Ligue1", "UCL", "WorldCup"]
    tier_b_leagues = ["JLeague", "KLeague", "MLS", "Championship", "Eredivisie"]
    
    trainer = FusionTrainer(limit=None)
    
    print("🚀 开始训练分联赛垂直模型 (Tier 分层隔离)...")
    t0 = time.time()
    
    for tier_name, leagues in [("TierA", tier_a_leagues), ("TierB", tier_b_leagues)]:
        print(f"\n[任务] 正在训练 {tier_name} 专属模型 (覆盖: {','.join(leagues)})...")
        try:
            w = trainer.train_tier(tier_name, leagues)
            if w:
                print(f"  ✅ 成功: Accuracy={w.accuracy:.4f}, Samples={w.sample_count}")
                path = w.save()
                print(f"  💾 已保存: {path}")
            else:
                print("  ⚠️ 失败: 样本不足或训练未收敛")
        except Exception as e:
            print(f"  ❌ 错误: {e}")
            
    print(f"\n🎉 所有模型训练完成！总耗时: {(time.time()-t0)/60:.1f} 分钟")

if __name__ == "__main__":
    # 需要设置环境变量
    train_all_leagues()
