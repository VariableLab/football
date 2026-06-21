"""快速全量LR重训 + class_weight + 跳过CV"""
import sys
import logging
import time
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s", stream=sys.stdout)

from fusion.fusion_trainer import FusionTrainer
from fusion.logistic_fusion import LogisticFusionTrainer

print("=== Step 1: 构建特征 (30,887 样本) ===")
trainer = FusionTrainer(limit=None)
t0 = time.time()
X, y = trainer._build()
t1 = time.time()
print(f"特征构建: {len(X)} 样本, {X.shape[1]} 维, 耗时 {(t1-t0)/60:.1f} 分钟")
print(f"标签分布: home={sum(y==0)} draw={sum(y==1)} away={sum(y==2)}")

print("\n=== Step 2: 全量 LR 训练 (Balanced) ===")
t = LogisticFusionTrainer(l1_penalty=0.001, max_iter=1000, class_weight=None)
t2 = time.time()
w = t.fit(X, y, league="global")
t3 = time.time()
path = w.save()
print(f"\n训练完成: 耗时 {(t3-t2)/60:.1f} 分钟")
print(f"  Accuracy: {w.accuracy:.4f}")
print(f"  Cross-entropy: {w.cross_entropy:.4f}")
print(f"  Samples: {w.sample_count}")
print(f"  保存到: {path}")
