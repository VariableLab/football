# 快速修复指南

> **紧急程度**: 🔴 高  
> **预计耗时**: 2-4 小时  
> **前置条件**: Python 3.8+, 已安装依赖  

---

## 📋 问题概览

根据 2026-05-19 审计报告，系统存在以下关键问题：

| 问题 | 优先级 | 影响 | 状态 |
|------|--------|------|------|
| 赔率采集系统停滞 | P0 | 预测质量下降 | 🔴 待修复 |
| LR融合权重加载失败 | P0 | 模型版本混乱 | 🔴 待修复 |
| 残差NN数值不稳定 | P0 | 残差修正失效 | 🔴 待修复 |

---

## 🚀 快速修复步骤

### 步骤 1: 运行紧急修复脚本

```bash
cd backend
python emergency_fix.py
```

**预期输出**：
```
==============================================================
竞彩足球预测系统 - 紧急修复脚本
审计日期: 2026-05-19
==============================================================

步骤 1: 检查赔率数据源状态
测试 zgzcw.com 数据源...
zgzcw 结果: {'updated': 24, 'matches': 164}
✅ 至少一个赔率数据源可用

步骤 2: 验证 LR 融合权重文件
权重目录: /path/to/backend/data/weights/lr
✅ 找到 3 个权重文件
✅ 权重加载成功:
  - 准确率: 54.27%
  - 样本数: 28000
  - 训练时间: 2026-05-17 17:30:00

步骤 3: 修复残差 NN 数值不稳定
✅ 残差 NN 特征标准化已修复

步骤 4: 重新生成预测（使用 LR 融合）
找到 164 场待处理比赛
✅ 预测重新生成完成: 成功 10, 失败 0

==============================================================
修复结果总结
==============================================================
赔率数据源检查: ✅ 成功
LR权重验证: ✅ 成功
残差NN修复: ✅ 成功
预测重新生成: ✅ 成功

==============================================================
✅ 所有紧急修复项已完成
==============================================================
```

---

### 步骤 2: 手动修复（如果脚本失败）

#### 2.1 修复赔率采集

如果赔率数据源失效，需要手动检查：

```bash
# 测试 zgzcw 数据源
cd backend
python -c "
from zgzcw_source import collect_zgzcw_odds
from models import SessionLocal
db = SessionLocal()
result = collect_zgzcw_odds(db)
print(result)
db.close()
"
```

**可能的问题**：
- 反爬虫限制 → 添加代理池
- 网站结构变化 → 更新爬虫逻辑
- 网络连接问题 → 检查网络

**临时解决方案**：
```bash
# 启用合成赔率兜底
# 编辑 backend/odds_collector.py
# 在 collect_odds_tier1_primary() 中添加：
if result.get('updated', 0) == 0:
    logger.warning("赔率采集失败，启用合成赔率")
    generate_synthetic_odds(db)
```

---

#### 2.2 修复 LR 权重加载

如果权重文件不存在：

```bash
# 检查权重文件
ls -lh backend/data/weights/lr/global_*.json

# 如果不存在，运行融合训练
cd backend
python fusion/fusion_trainer.py --train-global --epochs 100
```

**预期输出**：
```
[fusion-trainer] 开始全局 LR 融合训练
[fusion-trainer] 加载训练数据: 28000 场比赛
[fusion-trainer] 特征维度: 43
[fusion-trainer] 训练完成: 准确率 54.27%, Brier Score 0.185
[fusion-trainer] 权重已保存: data/weights/lr/global_v1_2026-05-19.json
```

---

#### 2.3 修复残差 NN

如果残差 NN 数值不稳定：

```bash
# 编辑 backend/residual_nn.py
# 找到特征标准化部分（约第 150 行）
# 修改为：

feature_std = np.std(features, axis=0)
feature_std = np.where(feature_std == 0, 1.0, feature_std)  # 避免除零
normalized = (features - feature_mean) / feature_std

# 添加有效性检查
if np.any(np.isnan(normalized)) or np.any(np.isinf(normalized)):
    logger.error("[ResidualNN] Invalid features detected, skipping correction")
    return spf  # 返回原值
```

**重新训练**：
```bash
cd backend
python residual_nn.py --train --epochs 100
```

---

### 步骤 3: 验证修复结果

```bash
# 检查赔率覆盖率
cd backend
python -c "
from models import SessionLocal, Match, MatchStatus
db = SessionLocal()
total = db.query(Match).filter(Match.status.in_([MatchStatus.SCHEDULED, MatchStatus.UPCOMING])).count()
with_odds = db.query(Match).filter(
    Match.status.in_([MatchStatus.SCHEDULED, MatchStatus.UPCOMING]),
    Match.odds_home.isnot(None)
).count()
print(f'赔率覆盖率: {with_odds}/{total} ({with_odds/total*100:.1f}%)')
db.close()
"

# 检查 LR 融合使用率
python -c "
from models import SessionLocal, Prediction
db = SessionLocal()
total = db.query(Prediction).count()
lr_count = db.query(Prediction).filter(Prediction.model_version.like('%lr%')).count()
print(f'LR 融合使用率: {lr_count}/{total} ({lr_count/total*100:.1f}%)')
db.close()
"

# 检查残差 NN 状态
python -c "
from residual_nn import ResidualPredictor
predictor = ResidualPredictor()
if predictor.is_ready():
    print('✅ 残差 NN 已就绪')
else:
    print('❌ 残差 NN 未就绪')
"
```

**预期结果**：
```
赔率覆盖率: 131/164 (79.9%)
LR 融合使用率: 156030/156030 (100.0%)
✅ 残差 NN 已就绪
```

---

### 步骤 4: 重启服务

```bash
# 停止现有服务
pkill -f "uvicorn main:app"

# 重启服务
cd backend
nohup uvicorn main:app --host 0.0.0.0 --port 8000 > logs/uvicorn.log 2>&1 &

# 检查服务状态
curl http://localhost:8000/api/health
```

**预期输出**：
```json
{
  "status": "ok",
  "version": "0.1.0",
  "timestamp": "2026-05-19T17:00:00Z"
}
```

---

## 📊 验收标准

修复完成后，应满足以下标准：

| 指标 | 目标 | 验证方法 |
|------|------|---------|
| 赔率覆盖率 | ≥ 80% | 查询数据库 |
| LR 融合使用率 | 100% | 查询 predictions 表 |
| 残差 NN 成功率 | ≥ 95% | 检查日志 |
| 预测生成无报错 | 100% | 检查日志 |

---

## 🔍 故障排查

### 问题 1: 赔率采集仍然失败

**症状**：`collect_zgzcw_job()` 返回 `{'updated': 0, 'matches': 164}`

**可能原因**：
1. 网站反爬虫限制
2. 网站结构变化
3. 网络连接问题

**解决方案**：
```bash
# 1. 检查网络连接
curl -I https://www.zgzcw.com

# 2. 检查爬虫日志
tail -f backend/logs/zgzcw.log

# 3. 手动测试爬虫
cd backend
python zgzcw_source.py --test
```

---

### 问题 2: LR 权重加载失败

**症状**：日志中出现 `[LR-fusion] Failed to load weights`

**可能原因**：
1. 权重文件不存在
2. 权重文件损坏
3. 路径配置错误

**解决方案**：
```bash
# 1. 检查权重文件
ls -lh backend/data/weights/lr/

# 2. 验证权重文件完整性
cd backend
python -c "
from fusion.logistic_fusion import LogisticFusionWeights
import glob
files = sorted(glob.glob('data/weights/lr/global_*.json'))
if files:
    w = LogisticFusionWeights.load(files[-1])
    print(f'权重加载成功: 准确率 {w.accuracy:.2%}')
else:
    print('未找到权重文件')
"

# 3. 重新训练权重
python fusion/fusion_trainer.py --train-global
```

---

### 问题 3: 残差 NN 仍然报错

**症状**：日志中出现 `RuntimeError: invalid value encountered in divide`

**可能原因**：
1. 特征标准化未修复
2. 输入数据异常
3. 模型权重损坏

**解决方案**：
```bash
# 1. 检查代码是否已修复
grep "feature_std = np.where" backend/residual_nn.py

# 2. 重新训练模型
cd backend
python residual_nn.py --train --epochs 100

# 3. 验证模型
python residual_nn.py --test
```

---

## 📞 联系支持

如果以上步骤无法解决问题，请：

1. 收集日志文件：
   ```bash
   tar -czf logs_$(date +%Y%m%d).tar.gz backend/logs/ backend/emergency_fix.log
   ```

2. 记录错误信息：
   - 错误日志
   - 执行的命令
   - 系统环境信息

3. 提交 Issue 或联系开发团队

---

## 📚 相关文档

- [完整审计报告](AUDIT_REPORT_20260519.md)
- [整改计划](REMEDIATION_PLAN.md)
- [架构设计](ARCHITECTURE_V2.md)
- [自动化方案](AUTOMATION.md)

---

**最后更新**: 2026-05-19 17:00  
**下次审计**: 2026-05-26
