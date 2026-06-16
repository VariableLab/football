# WC Analytics 审计存档 — 2026-06-16

> 本目录是 2026-06-16 一次完整审计(静态 + 动态)的落盘基线。
> 用于后续做月度对比、问题追踪、版本回滚参照。

## 文件清单

| 文件 | 用途 | 审计方式 | 时间戳 |
|------|------|---------|--------|
| [`AUDIT_STATIC_20260616.md`](./AUDIT_STATIC_20260616.md) | **静态基线** — 文档与代码的离线审计 | 只读静态分析 | 2026-06-16 22:35 |
| [`AUDIT_DYNAMIC_20260616.md`](./AUDIT_DYNAMIC_20260616.md) | **动态基线** — 对生产站与 API 的实时探测 | 实时 HTTP 探测 | 2026-06-16 22:46 |

## 为什么要双基线

1. **静态审计**回答: "代码与文档说它做了什么?"
2. **动态审计**回答: "生产站实际上在做什么?"

通过两套视角的**对照**(见动态报告第 8 节),可以发现"文档/实际脱节"这类仅靠单一视角无法暴露的问题。

## 与历史审计的对应关系

| 历史审计 | 文件位置 | 备注 |
|---------|---------|------|
| 5-19 审计 (Kiro) | `docs/AUDIT_REPORT_20260519.md` | 首次提出 5 P0 + 5 P1 |
| 5-26 综合审计 | `docs/COMPREHENSIVE_AUDIT_20260526.md` | 静态综合,定级 5.2/10 |
| **6-16 静态 (本目录)** | `AUDIT_STATIC_20260616.md` | 与 5-26 评分持平,子项有显著变化 |
| **6-16 动态 (本目录)** | `AUDIT_DYNAMIC_20260616.md` | **颠覆 5-26 "502" 结论,发现新问题** |

## 后续跟踪

- **下次静态审计**: 建议 2026-07-15(每月一次)
- **下次动态审计**: 建议 2026-07-01、2026-07-15(双周一次更稳)
- **基线对比方法**: 同时阅读新旧两份"动态报告第 8 节"(与历史报告矛盾点)
- **P0 修复追踪**: 见 `docs/REMEDIATION_PLAN.md`(待与本次审计合并)

## 复现命令

```bash
# 静态审计复现: 阅读本文档 + docs/COMPREHENSIVE_AUDIT_20260526.md
# 动态审计复现: 用 web_fetch 探测下列端点
curl https://football.nett.to/api/health
curl https://football.nett.to/api/matches?status=upcoming&limit=3
curl https://football.nett.to/api/matches/1
curl https://football.nett.to/api/jingcai/issue
curl https://football.nett.to/api/feedback
curl https://football.nett.to/api/admin/dashboard
```

---

*建立时间: 2026-06-16 23:14 | 维护人: AI 审计 agent | 版本: 1.0*
