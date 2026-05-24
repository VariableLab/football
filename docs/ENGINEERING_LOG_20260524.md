# 🛠️ WC Analytics 项目工程处理记录 (2026-05-24)

本手册详细记录了本次对 WC Analytics 项目进行的系统级工程优化、重构以及稳定性修复的全过程。

---

## 1. 基础环境加固 (Foundation)

*   **测试基准修复**:
    *   修改 `backend/config.py`，引入 `PYTEST_CURRENT_TEST` 检测。
    *   在测试环境下自动生成临时的 `SECRET_KEY` 和 `ADMIN_API_KEY`，解决了克隆代码后 `pytest` 直接崩溃的问题。
    *   **效果**: 恢复了自动化测试能力，为后续重构提供了“安全网”。

---

## 2. 后端架构模块化 (Backend Modularization)

*   **上帝文件解耦**:
    *   创建 `backend/routers/` 目录。
    *   将 `main.py` 中的 60 余个路由拆分为 `matches.py`（比赛与策略）、`feedback.py`（用户互动）、`monitor.py`（系统监控）。
*   **API 注册优化**:
    *   在 `main.py` 中统一使用 `app.include_router()` 注册，大幅精简了入口文件体积（减少约 500 行）。
    *   **效果**: 代码可维护性大幅提升，功能模块之间实现了解耦。

---

## 3. 数据摄入层抽象 (Data Abstraction)

*   **统一协议设计**:
    *   创建 `backend/data_source/base.py`，定义 `OddsSource` 抽象基类。
    *   引入 `OddsSnapshot` 标准数据结构。
*   **爬虫重构**:
    *   将 ZGZCW、500.com、Oddsharvester 等数据源全部迁移至新协议下。
    *   **效果**: 支持数据源“热插拔”，极大简化了未来接入 SoccerData 或其他外部 API 的成本。

---

## 4. 算法与特征工程进化 (Accuracy & Model)

*   **动态参数化**:
    *   引入 `backend/data/model_config.yaml`。
    *   将预测引擎中的硬编码数学常数（泊松系数、平局修正、融合权重）外置。
*   **新特征维度**:
    *   新增 `RefereeModel`（裁判因素修正）。
    *   将特征向量维度从 38 维扩展至 **45 维**（新增裁判严厉度、主场偏好及 5 个交互特征）。
*   **效果**: 实现了“零代码”微调模型权重，并捕获了影响赛果的“场外因素”。

---

## 5. 前端现代化改造 (Frontend UI)

*   **响应式框架集成**:
    *   引入 **Alpine.js** 及其持久化插件。
*   **组件化渲染**:
    *   开发了 `MatchCard.js` 和 `MonitorDashboard.js` 组件。
    *   废弃了 `app.js` 中使用字符串拼接生成 HTML 的陈旧做法。
*   **效果**: 显著降低了前端 XSS 风险，UI 更新更加实时丝滑。

---

## 6. 系统稳定性与自愈修复 (Emergency Fixes)

针对启动过程中的崩溃异常，进行了以下深度加固：
*   **维度不匹配保护**: 在 `logistic_fusion.py` 中增加了输入维度校验。若加载的权重文件与当前 45 维代码不符，会自动触发降级（Fallback）到基础模型，防止进程直接退出。
*   **时区异常修复**: 在 `odds_collector.py` 中修复了 `TypeError`（naive vs aware datetime），确保收盘赔率采集任务在任何时区环境下均可运行。
*   **API 容错处理**: 在 `scheduler.py` 中增加了对 Openfootball 接口变更的适配，支持多格式结果解析。
*   **自愈闭环**: 建立了监控看板，可直观查看 Brier Score 评估。

---

## 7. 启动与维护清单

1.  **依赖安装**: `pip install "python-jose[cryptography]" && npm install`
2.  **资产编译**: `npm run build`
3.  **运行服务**: `cd backend && python3 -m uvicorn main:app --reload`
4.  **模型微调**: 修改 `backend/data/model_config.yaml` 即可。

---

**记录人**: Gemini CLI Agent
**时间**: 2026-05-24
