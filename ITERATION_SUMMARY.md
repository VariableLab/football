# Football 预测项目迭代总结

> 生成日期: 2026-06-25
> 当前 SPF 准确率: 53.9%, Brier=0.191
> 目标: SPF ≥ 55%

---

## 一、已完成工作

### 1. PostgreSQL 迁移
- SQLite → PostgreSQL (129.146.124.72)
- 31,553 场比赛, 173,691 条预测, 614 支球队
- 修复: group→group_name 保留字, VARCHAR 长度, 序列不同步
- config.py 默认驱动改为 `postgresql://` (同步), 非 `postgresql+asyncpg://`

### 2. 代码重构
- `scheduler.py` (1606行) → `monitor/scheduler/` 包 (8 个模块)
- `odds_collector.py` (1589行) → `ingestion/` 包 (11 个模块)
- 向后兼容 shim 导入保持现有代码不崩

### 3. 服务修复
- health.py: scheduler 导入路径, odds_freshness 查询表, alert_manager 路径, datetime 时区
- match_ops.py: numpy 类型 JSON 序列化 → `_convert_numpy()`
- health_daemon.py: `from scheduler` → `from monitor.scheduler`
- main.py: `from scheduler` → `from monitor.scheduler`

### 4. 本地训练环境
- Python 3.13.3 + venv313
- PyTorch 2.12.1 with MPS (Apple Silicon GPU)
- 6 个模型全部训练成功
- 权重同步到服务器

### 5. P0 准确率修复
- Fusion LR 数据质量门控: 排除友谊赛/abandoned/no odds
- OneCycleLR step 位置修复 (从 epoch 后移到 batch 内, 防止死锁)
- Draw Detection: threshold 0.65→0.45, max_boost 0.05→0.10
- NN 修正权重降低: StackingNet 60%→20%, BetNN 50%→20%
- LR 验证集扩大: val_ratio 0.1→0.2
- FEATURE_NAMES 45→53 补全

### 6. 特征数据补全
- sync_features.py: xg/rest/injury/env 四个同步步骤
- rest_days: 272/614 支球队从默认值 7 更新为真实值
- avg_xg: 已填充 (Elo 回归 fallback)

---

## 二、当前准确率基线

| 玩法 | 准确率 | Brier | 样本量 |
|------|--------|-------|--------|
| SPF 胜平负 | **53.9%** | 0.1911 | 31,234 |
| RQ 让球 | 46.6% | 0.2161 | 31,234 |
| Score 比分 | 12.1% | - | 31,234 |
| Goals 总进球 | 22.7% | - | 31,234 |
| Half 半全场 | 34.4% | - | 23,576 |

League 细分 (SPF):
- EPL: 56.7% (最佳)
- SerieA: 54.5%
- LaLiga: 52.9%
- Ligue1: 52.9%
- Bundesliga: 52.5% (最差)

---

## 三、已知问题与瓶颈

### P0: LR 权重退化
- 旧权重 (May): 57.4% accuracy, Brier=0.913
- 新权重 (Jun): 48.0% accuracy, Brier=1.321
- 原因: 样本从 5,000 扩大到 31,228 时引入低质量数据, L1 正则化为 0
- **当前策略**: 保留旧权重, 新权重未通过 A/B 验证

### P1: 特征空洞
- avg_xg/avg_xga: 87% 缺失 → 回退到 avg_goals_scored
- possession/pass_completion/shots: 87% 缺失
- key_injuries: 70% 缺失
- rest_days: 59% 为默认值 7 → 已修复 272 支
- weather/pitch/venue_type: 81% 缺失

### P2: 预测流水线信号稀释
- 5 层变换: Elo→Poisson→LR Fusion→StackingNN→BetNN→Lab Override
- Lab Elo Override 覆盖 90% 计算 → 已降为 30-50%
- StackingNet 60% → 已降为 20%
- BetNN 50% → 已降为 20%

### P3: 置信度标注
- 173,691 条预测中 99.7% confidence 为空
- 新预测会自动带 confidence
- 历史数据需 backfill (scripts/backfill_confidence.py)

### P4: 小联赛样本不足
- Allsvenskan 仅 12 场, FIFA World Cup 系列各 15-16 场
- 国际友谊赛 48 场 (已过滤)

### P5: 验证集过小
- Fusion LR 验证集仅 ~21 场 (val_ratio=0.2)
- A/B 验证统计效力不足
- 需要更多历史数据

---

## 四、下一步计划

### 短期 (1-2 周)
1. **等待新预测积累** — 当前准确率基于历史预测, 新代码对新预测生效
2. **运行 backfill_confidence.py** — 补全历史预测的 confidence 字段
3. **扩大训练数据** — 收集更多历史比赛用于 LR 训练

### 中期 (2-4 周)
4. **补全 possession/pass_completion** — 需要数据源 (FBref/SofaScore)
5. **联赛分层建模** — EPL/SerieA 等高数据质量联赛单独训练
6. **Draw Classifier 重训** — val_acc 仅 8.6%, 需要更多平局特征

### 长期 (1-3 月)
7. **简化预测管线** — 收敛到 3-4 层变换
8. **引入外部数据源** — injury API, weather API, referee data
9. **概率校准** — Platt Scaling / Isotonic Regression 替代 NN 修正

---

## 五、部署信息

### 服务器
- IP: 129.146.124.72
- SSH: `ssh -i ~/.ssh/server_key -p 22 ubuntu@129.146.124.72`
- 项目: `/home/ubuntu/Github/football`
- Python: 3.13, venv at `backend/venv`
- 服务: `sudo systemctl restart football.service`
- Health: `curl -s http://127.0.0.1:8000/api/health`
- PostgreSQL: `postgresql://postgre:prefect@129.146.124.72:5432/football`

### 本地
- Python: 3.13.3, venv313 at `backend/venv313`
- PyTorch: 2.12.1 with MPS
- 训练: `cd backend && source venv313/bin/activate && python train_all.py`
- 验证: `PYTHONPATH=. python scripts/validate_predictions.py`
- 特征同步: `python sync_features.py --step xg rest`

### GitHub
- Repo: https://github.com/VariableLab/football
- 分支: master

---

## 六、关键文件索引

| 文件 | 用途 |
|------|------|
| `backend/main.py` | FastAPI 入口, sys.path 设置 |
| `backend/database/config.py` | DATABASE_URL, DB_POOL 配置 |
| `backend/database/models.py` | ORM 模型定义 |
| `backend/core/prediction_engine.py` | 主预测引擎 (5 层管线) |
| `backend/core/prediction_nn_correction.py` | NN 修正层 (已简化) |
| `backend/fusion/validate_deploy.py` | LR A/B 验证部署 |
| `backend/fusion/logistic_fusion.py` | LR 训练, FEATURE_NAMES |
| `backend/fusion/fusion_trainer.py` | 数据质量门控 |
| `backend/core/residual_nn.py` | StackingNN 训练 (OneCycleLR) |
| `backend/core/draw_classifier.py` | 平局分类器 (已优化阈值) |
| `backend/monitor/scheduler/__init__.py` | APScheduler 注册所有 job |
| `backend/monitor/scheduler/match_ops.py` | 预测锁定, numpy 转换 |
| `backend/ingestion/sync_features.py` | 特征数据同步 |
| `backend/train_all.py` | 全量训练入口 |
| `backend/scripts/validate_predictions.py` | 准确率验证 |
| `backend/scripts/backfill_confidence.py` | 置信度历史补全 |
| `backend/api/routers/health.py` | Health check API |

---

## 七、常见故障排查

### 服务启动失败
1. 检查 `journalctl -u football.service --since "1 min ago" | tail -50`
2. 清理缓存: `find backend -name '*.pyc' -delete && find backend -name '__pycache__' -exec rm -rf {} +`
3. 检查 import: `cd backend && source venv/bin/activate && python -c "from main import app"`

### 训练卡死
1. NN 训练卡死 → OneCycleLR step 位置 (已在 batch 内)
2. Fusion LR 特征工程慢 → 31,000+ 场比赛, 正常
3. 网络断开 → 检查 PostgreSQL 连接

### 准确率不提升
1. validate_predictions.py 验证的是历史预测, 新代码对新预测生效
2. 需要等新比赛产生预测后才能看到提升
3. 检查 confidence 写入: `db.query(Prediction).filter(Prediction.confidence.isnot(None)).count()`

### 告警过多
```bash
echo '[]' > ~/Github/football/backend/monitor/data/alerts.json
```
