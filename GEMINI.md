# Football Prediction Research Project AI 协作规范

## 项目性质
这是一个开源的机器学习方法论研究项目，目标是提供公开、可复现的足球预测模型对比与回测框架。

## 防数据泄漏铁律 (最高优先级)
- **特征计算隔离**: 任何特征在计算时间 T 时，绝对不能引用 T 之后的数据。
- **回测机制**: 统一使用“时序前向验证”（Walk-forward validation），严禁使用随机 K 折交叉验证。
- **泄漏测试**: 新增特征时，必须编写单元测试验证其时序安全性。

## 模型开发规范
- **统一接口**: 所有模型必须继承 `BasePredictor` 抽象基类，实现 `fit(X, y)` 和 `predict_proba(X)` 接口。
- **基准对比**: 任何新模型必须与 `baseline.py` (如主队胜、市场概率) 进行对比。
- **概率校准**: 除了准确率，必须报告 RPS (Ranked Probability Score) 和 Brier Score。

## 产品方向: 2026 世界杯赛事内容引擎 (MVP)
- **目标**: 为每场比赛自动生成前瞻数据卡、xG 分析和 AI 解说。
- **核心组件**:
    - `src/footy/data/statsbomb.py`: 提取细粒度事件数据。
    - `src/footy/data/football_data_org.py`: 赛程与实时比分。
    - `src/footy/content/engine.py`: 内容合成逻辑。
- **输出格式**: JSON 结构化数据，可对接 Canvas/SVG 生成图文内容卡。
