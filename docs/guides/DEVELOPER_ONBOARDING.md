# WC Analytics — 开发者入门指南

> 最后更新: 2026-06-21

## 项目是什么

WC Analytics 是一个足球比赛预测平台,核心能力:
1. 从多个数据源采集比赛和赔率数据
2. 运行三层融合预测模型生成概率
3. 基于概率生成带风控的投注策略建议
4. 通过 REST API 提供服务

## 技术栈

| 层 | 技术 |
|---|---|
| 后端 | FastAPI (Python 3.10) |
| 数据库 | PostgreSQL (生产) / SQLite (开发) |
| 调度 | APScheduler (35个定时任务) |
| 前端 | Alpine.js + Tailwind CSS 4 |
| ML | NumPy, SciPy, PyTorch (NN子模型) |

## 快速启动

```bash
cd backend
pip install -r requirements.txt

# 开发环境 (SQLite)
python main.py

# 生产环境 (PostgreSQL)
export DATABASE_URL=postgresql://user:pass@host/db
export SECRET_KEY=<32字符以上随机字符串>
export ADMIN_API_KEY=<管理员API密钥>
uvicorn main:app --host 0.0.0.0 --port 8000
```

## 核心架构

```
数据源 → 采集器 → 数据库 → 预测引擎 → 策略管线 → API → 前端
                                    ↓
                              模型验证 → 重训练
```

### 预测引擎三层

1. **Layer 1 — 物理模型**: Elo实力 + Dixon-Coles泊松 + 8种修正因子
2. **Layer 2 — 统计融合**: LR逻辑回归(48维特征) 或 线性加权回退
3. **Layer 3 — 神经修正**: Stacking NN残差修正

### 策略管线

校准 → 边际计算 → 过滤 → 凯利仓位 → 风控检查 → 输出

## 代码结构

```
backend/
├── main.py              # FastAPI 入口
├── api/                 # REST API
│   ├── routers/         # 17个路由模块
│   ├── auth.py          # JWT认证
│   └── schemas.py       # Pydantic模型
├── core/                # 预测引擎 + 子模型
├── features/            # 特征工程 + 子模型
├── fusion/              # LR融合 + 验证部署
├── database/            # ORM模型 + 配置
├── strategy/            # 策略管线 + 风控
├── ingestion/           # 数据采集器
├── monitor/             # 调度器 + 监控
├── scripts/             # 工具脚本
└── data/                # 配置文件 + 权重
```

## 关键文件

| 文件 | 作用 | 行数 |
|------|------|------|
| `core/prediction_engine.py` | 主预测引擎 | 2604 |
| `monitor/scheduler.py` | 35个定时任务 | 1614 |
| `database/models.py` | 20+张表ORM | 647 |
| `api/schemas.py` | API请求/响应模型 | 723 |
| `strategy/strategy_pipeline.py` | 策略管线 | 521 |

## 数据源优先级

1. **zgzcw.com** — 主力,54%覆盖,免费
2. **sporttery.cn** — 竞彩官方,每日同步
3. **the-odds-api** — 付费($29/月),目标80%+真实覆盖
4. **synthetic** — 合成兜底,目标<5%

## 模型版本

详见 [MODEL_VERSION_MAP.md](../guides/MODEL_VERSION_MAP.md)

**当前生产**: v2.0 线性加权  
**待部署**: v2.1 LR融合

## 常见任务

### 查看健康状态
```bash
curl https://football.nett.to/api/health
```

### 列出比赛
```bash
curl "https://football.nett.to/api/matches?status=upcoming&limit=5"
```

### 单场策略
```bash
curl "https://football.nett.to/api/matches/1/strategy?risk_tier=balanced"
```

### 运行测试
```bash
cd backend
pytest tests/ -v
```

## 注意事项

- 所有模型预测在赛前锁定,赛后不修改
- 调度器任务自动运行,无需手动触发
- 权重文件在 `data/weights/lr/`, 每次训练后部署
- 备份在 `backup/` 目录,每日凌晨自动执行
