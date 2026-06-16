# 🤖 WC Analytics Pro Agent 操作手册 (v5.0)

本项目已集成智能管理 Agent，允许通过 Telegram 以**自然语言**远程管理整个量化系统。

## 📱 连接方式
1. 打开 Telegram，搜索你的机器人（使用在 `.env` 中配置的 Bot Token）。
2. 发送任何指令。机器人仅响应 `.env` 中指定的管理员 Chat ID 或白名单 ID 的请求。

---

## 🛠️ 核心管理指令 (暗号)

| 指令关键词 | 动作说明 | 背后逻辑 |
| :--- | :--- | :--- |
| **“战报” / “验证”** | 获取实时回测报告 | 运行 `ValidationEngine` 分析最近 100 场真实赛果的命中率。 |
| **“重训” / “修复”** | 强制执行模型重训练 | 物理清理旧权重，对齐 59 维特征，启动 `StackingTrainer`。 |
| **“机会” / “扫描”** | 寻找极端市场偏差 | 调取 `AgentTools` 扫描 Edge > 12% 的焦点场次。 |
| **“状态” / “健康”** | 检查系统运行状况 | 汇总 `HealthDaemon` 的自检结果（赔率鲜度、数据流）。 |

---

## 📡 生产环境运维命令 (服务器 SSH 使用)

系统已配置 **systemd** 守护进程，可确保 7x24 小时运行。

- **查看 API 状态**：`sudo systemctl status football-api`
- **查看 Agent 日志**：`sudo journalctl -u football-agent -f`
- **重启全套服务**：`sudo systemctl restart football-api football-agent`

---

## ⚖️ 模型核心参数说明
- **输入维度**：59 维 (包含基础统计、交互项及残差修正项)。
- **目标联赛**：2026 世界杯、主流欧洲五大联赛、洲际杯赛。
- **准确率基准**：当前生产环境已对齐至 **55.7%**。

---
*First Principles Research Lab © 2026*
