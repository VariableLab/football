# 会话记录 JSON 文件说明

## 目录

`data/session_logs/` — 存放每次开发会话的结构化记录，用于 AI 模型训练。

## 文件命名规则

```
session_{日期}_{主题}.json
```

示例：`session_20260514_data_cleaning.json`

## JSON 结构说明

| 字段 | 层级 | 类型 | 说明 |
|------|------|------|------|
| `session_id` | 顶层 | string | 会话唯一标识，格式 `{日期}-{主题}` |
| `session_date` | 顶层 | string | 会话日期 YYYY-MM-DD |
| `project` | 顶层 | string | 项目名称 |
| `project_type` | 顶层 | string | 项目类型标识 |
| `project_repo` | 顶层 | string | 代码仓库路径 |
| `session_goal` | 顶层 | string | 本次会话的核心目标 |
| `session_summary` | 顶层 | string | 一句话总结成果 |
| `project_status` | 顶层 | object | 项目当前状态快照（准确率、模型数等） |
| `issues_found` | 顶层 | array | 发现的问题列表 |
| `new_features` | 顶层 | array | 新增功能列表 |
| `fixes_summary` | 顶层 | object | 修复统计汇总 |
| `remaining_issues` | 顶层 | array | 遗留问题列表 |
| `architecture` | 顶层 | object | 架构变更前后对比 |
| `files_modified` | 顶层 | array | 修改的文件清单 |
| `for_model_training` | 顶层 | object | 模型训练专用标注 |

## issues_found 每条结构

| 字段 | 说明 |
|------|------|
| `id` | 问题编号 (ISS-XXX) |
| `category` | 分类: timezone / odds_dedup / safe_float / team_name / controlled_vocab / pre_write_validation |
| `severity` | 严重度: critical / warning / info |
| `title` | 问题标题 |
| `description` | 详细描述 |
| `affected_files` | 受影响的文件列表，含 path/lines/problem |
| `fix_method` | 修复方案描述 |
| `fix_files` | 修改的文件列表 |
| `result` | 修复结果 |

## new_features 每条结构

| 字段 | 说明 |
|------|------|
| `id` | 功能编号 (FEAT-XXX) |
| `title` | 功能名称 |
| `description` | 功能描述 |
| `details` | 技术实现细节 dict |
| `files` | 涉及文件列表 |

## for_model_training 字段说明

此字段专供模型训练使用：

| 字段 | 说明 |
|------|------|
| `intent` | JSON 文件用途说明 |
| `key_patterns` | 本次会话体现的关键技术模式/教训 |
| `labels` | 标注信息：task_type、complexity、scope、technologies、problem_categories |

## 使用方式

1. **训练输入**：每个 JSON 文件作为一条训练样本
2. **训练目标**：让模型学会识别数据质量问题 → 选择修复策略 → 生成代码变更
3. **上下文关联**：`session_id` 可跨文件串联同一项目的多次迭代
4. **标签过滤**：`for_model_training.labels` 支持按技术栈、问题类别筛选样本

## 写入规范

- 每次会话结束时自动生成 JSON 文件
- `issues_found` 必须包含完整的技术细节（文件路径、行号、具体问题）
- `fix_method` 必须描述"做了什么"而非"应该做什么"
- `for_model_training.key_patterns` 提炼可泛化的模式，不局限于本项目
- JSON 必须合法，可用 `python3 -c "import json; json.load(open(f))"` 验证
