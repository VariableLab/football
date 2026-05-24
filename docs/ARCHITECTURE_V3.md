# WC Analytics — 架构手册 v3.0

> 最后更新: 2026-05-24 (重构版)

## 1. 核心架构变更

本项目在 2026-05-24 进行了大规模架构升级，从“巨石应用”转向“模块化服务”架构。

### 1.1 后端模块化 (Modular Routing)
- **解耦**: `main.py` 不再处理具体业务逻辑，仅作为应用入口。
- **路由器**: 引入 `backend/routers/` 目录。
  - `matches.py`: 核心数据流。
  - `feedback.py`: 互动功能。
  - `monitor.py`: 性能审计。

### 1.2 数据摄入抽象层 (Data Ingestion Layer)
- **协议化**: 所有外部数据源必须实现 `data_source.base.OddsSource` 接口。
- **标准化**: 引入 `OddsSnapshot` 模型，统一不同站点的赔率数据格式。

### 1.3 动态配置化 (Dynamic Tuning)
- **实时性**: 模型核心参数（泊松系数、融合权重）迁移至 `backend/data/model_config.yaml`。
- **热加载**: 支持无需重启服务即可微调模型。

### 1.4 前端组件化 (Reactive Frontend)
- **Alpine.js**: 引入响应式框架管理 UI 状态。
- **组件目录**: `static/src/components/` 包含可复用的 JS 组件。
- **声明式**: 废弃 `innerHTML` 拼接，改用 `x-for` 等模板指令。

---

## 2. 预测管线与反馈闭环

系统实现了自动化的反馈监控闭环：

```
[数据采集] -> [模型预测] -> [赛果同步]
     ^                           |
     |                       [性能审计] (Brier Score)
     |                           |
[参数微调] <-------(人工/自动)-------┘
```

- **Layer 1**: 基础实力（Elo）+ 统计（Poisson）。
- **Layer 2**: 特征工程（45 维特征向量，含裁判、伤病、交互特征）。
- **Layer 3**: 市场校准与逻辑回归融合。

---

## 3. 技术栈更新

| 类别 | 技术 |
| :--- | :--- |
| **后端** | FastAPI, SQLAlchemy 2.0, APScheduler |
| **前端** | Alpine.js, Tailwind CSS 4, esbuild |
| **配置** | PyYAML, python-dotenv |
| **算法** | PyTorch, SciPy, Numpy |

