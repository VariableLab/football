# Football Prediction Research (FPR)

> 足球预测研究项目：可复现的回测框架与严谨的模型对比。

本项目旨在将足球预测从“投注变现”转向“方法论研究”。我们关注的不是短期的盈利，而是预测模型在统计学上的严谨性、概率校准度以及时序安全性。

## 核心架构

- **数据层 (Data)**: 使用 `football-data.co.uk` 等公开数据集，确保任何人都能复现实验结果。
- **模型层 (Models)**: 统一的 `BasePredictor` 接口，支持基准模型（Elo, Poisson）与先进模型（Residual NN）的公平对决。
- **回测层 (Backtest)**: 采用 **时序前向验证 (Walk-forward Validation)**，严格模拟赛前预测场景，严禁数据泄漏。
- **评估层 (Evaluation)**: 使用 **RPS (Ranked Probability Score)** 和 **Brier Score** 作为核心指标，而非单纯的准确率。

## 快速开始

1. **安装依赖**:
   ```bash
   pip install pandas numpy requests
   ```

2. **运行基准实验**:
   ```bash
   python research/run_baseline.py
   ```

## 目录结构

```
research/
├── configs/           # 实验配置文件 (YAML)
├── data/              # 数据目录 (Raw & Processed)
├── src/footy/
│   ├── data/          # 数据加载与标准化
│   ├── features/      # 时序安全的特征工程
│   ├── models/        # 模型定义 (Base, Baseline, Elo...)
│   ├── backtest/      # 回测引擎 (核心逻辑)
│   └── evaluation/    # 学术评估指标
└── run_baseline.py    # 实验启动脚本
```

## 研究原则

1. **防数据泄漏**: 所有特征必须在比赛开始前生成。
2. **诚实的基线**: 必须打败“永远买主胜”和“历史频率”基线。
3. **概率可信**: 通过校准曲线验证模型给出的 60% 概率是否真实对应 60% 的发生率。

---

*这是一个开源研究项目，不构成任何投注建议。*
