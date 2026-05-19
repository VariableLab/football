# OpenClaw 管理后台操作手册

> **用途**：本手册供自动化系统（OpenClaw）或管理员使用，通过 Admin API 管理世界杯比赛数据、录入结果、生成卡密。
>
> **基础URL**：`https://your-domain.com/api/admin`
>
> **认证方式**：所有 Admin API 请求需在 Header 中携带 `X-API-Key`

```bash
X-API-Key: your-admin-api-key-change-me
```

---

## 1. 快速开始

### 1.1 环境检查

```bash
curl -H "X-API-Key:football-money" \
  https://your-domain.com/api/admin/dashboard
```

**预期返回**：
```json
{
  "total_matches": 0,
  "finished_matches": 0,
  "total_predictions": 0,
  "prediction_accuracy": 0,
  "total_users": 0,
  "paid_users": 0
}
```

---

## 2. 球队管理

### 2.1 录入球队（小组赛分组确定后执行）

```bash
POST /api/admin/teams
Content-Type: application/json
X-API-Key: your-admin-api-key

{
  "name": "阿根廷",
  "name_en": "Argentina",
  "code": "ARG",
  "flag": "🇦🇷",
  "fifa_rank": 1,
  "elo": 1985,
  "group": "A",
  "continent": "南美洲"
}
```

**说明**：
- `code`：FIFA 三字码，如 ARG、BRA、FRA
- `group`：大写字母 A-L（2026世界杯共12组）
- 在赛程未公布前，先把48支球队全部录入

### 2.2 查询球队列表

```bash
GET /api/admin/teams
GET /api/admin/teams?group=A   # 按组筛选
```

---

## 3. 比赛管理

### 3.1 创建比赛（赛程公布后执行）

```bash
POST /api/admin/matches
Content-Type: application/json
X-API-Key: your-admin-api-key

{
  "match_code": "WC2026-A1",
  "home_team_id": 1,
  "away_team_id": 2,
  "kickoff_at": "2026-06-15T20:00:00",
  "group": "A",
  "stage": "group",
  "venue": "阿兹特克体育场"
}
```

**字段说明**：

| 字段 | 说明 |
|------|------|
| `match_code` | 唯一编码，建议格式 `WC2026-{组}{场次}`，如 A1, A2, R16-1 |
| `home_team_id` | 主队ID（从 `/teams` 查询获得） |
| `away_team_id` | 客队ID |
| `kickoff_at` | 开球时间，ISO 8601 格式 |
| `stage` | `group` / `R32` / `R16` / `QF` / `SF` / `F` |

### 3.2 查询比赛列表

```bash
GET /api/admin/matches
GET /api/admin/matches?status=finished
GET /api/admin/matches?group=A
GET /api/admin/matches?stage=R16
```

**状态值**：`scheduled` / `upcoming` / `live` / `finished` / `postponed`

### 3.3 录入比赛结果（赛后执行）⭐

```bash
PATCH /api/admin/matches/{match_id}/result
Content-Type: application/json
X-API-Key: your-admin-api-key

{
  "actual_home_goals": 2,
  "actual_away_goals": 1
}
```

**系统行为**：
- 自动判断胜负平（`actual_outcome` = `home` / `draw` / `away`）
- 自动将比赛状态改为 `finished`
- 赛后该场比赛策略自动对所有用户开放

**⚠️ 重要**：这是赛后最关键的操作。比赛结束后尽快录入，用户才能看到预测对比。

### 3.4 更新赔率

```bash
PATCH /api/admin/matches/{match_id}/odds?odds_home=1.72&odds_draw=3.40&odds_away=4.80
X-API-Key: your-admin-api-key
```

**说明**：赛前更新竞彩赔率快照，用于回测和展示。

---

## 4. 预测策略管理

### 4.1 锁定赛前预测（赛前1-48小时执行）⭐

```bash
POST /api/admin/predictions
Content-Type: application/json
X-API-Key: your-admin-api-key

{
  "match_id": 1,
  "play_type": "spf",
  "probabilities": {
    "home": 0.62,
    "draw": 0.20,
    "away": 0.18
  },
  "model_version": "v1.0"
}
```

**玩法类型**：

| play_type | 说明 | probabilities 格式 |
|-----------|------|-------------------|
| `spf` | 胜平负 | `{"home": 0.62, "draw": 0.20, "away": 0.18}` |
| `rq` | 让球 | `{"home": 0.35, "draw": 0.28, "away": 0.37, "handicap": -1}` |
| `score` | 比分 | `{"2:1": 0.123, "1:0": 0.101, ...}` |
| `goals` | 总进球 | `{"0": 0.05, "1": 0.165, "2": 0.241, ...}` |
| `half` | 半全场 | `{"HH": 0.382, "HD": 0.185, ...}` |

**⚠️ 重要**：每场比赛每个玩法只需录入一次。录入后即为锁定快照，不可修改（用于赛后验证）。

### 4.2 查询已录入预测

```bash
GET /api/admin/predictions
GET /api/admin/predictions?match_id=1
```

---

## 5. 卡密管理

### 5.1 批量生成卡密

```bash
POST /api/admin/licenses/generate
Content-Type: application/json
X-API-Key: your-admin-api-key

{
  "license_type": "tournament",
  "count": 100
}
```

**license_type**：
- `tournament` — 届卡（解锁全部比赛）
- `match` — 单场卡（解锁指定比赛，需传 `match_id`）

**返回示例**：
```json
{
  "generated": 100,
  "keys": [
    {"key": "WC26-AB3D-9F2A-KL7M-PQ8R", "type": "tournament"},
    ...
  ]
}
```

### 5.2 查询卡密状态

```bash
GET /api/admin/licenses
GET /api/admin/licenses?used=false   # 只看未使用的
```

### 5.3 卡密分发流程

```
1. 生成卡密（/licenses/generate）
2. 导出 keys 列表
3. 上传到淘宝/微店/个人微信销售
4. 用户购买后在前端输入卡密兑换
5. 系统全自动开通，无需人工干预
```

---

## 6. 审计日志

### 6.1 查询日志

```bash
GET /api/admin/audit-logs?limit=50
GET /api/admin/audit-logs?match_id=1&data_type=odds
```

**data_type**：`odds` / `lineup` / `injury` / `prediction` / `player_stats`

---

## 7. 典型操作流程（每日）

### 赛前48小时
```
1. 检查比赛列表 → GET /matches
2. 录入/更新赔率 → PATCH /matches/{id}/odds
3. 录入预测快照 → POST /predictions（每个玩法各一次）
```

### 比赛结束后（30分钟内）
```
1. 录入比分 → PATCH /matches/{id}/result
2. 系统自动：状态变为 finished，策略对所有用户开放
3. 检查准确率 → GET /dashboard
```

### 每周
```
1. 生成卡密 → POST /licenses/generate
2. 导出未使用卡密 → GET /licenses?used=false
3. 分发到销售渠道
```

---

## 8. 错误码

| 状态码 | 含义 | 处理 |
|--------|------|------|
| 200 | 成功 | — |
| 400 | 请求参数错误 | 检查 JSON 字段 |
| 403 | Admin Key 错误 | 检查 X-API-Key Header |
| 404 | 资源不存在 | 检查 ID 是否正确 |
| 422 | 校验失败 | 检查字段类型和必填项 |

---

## 9. 附录：48队分组录入模板（2026）

请先调用 `POST /teams` 录入以下球队（按实际抽签结果调整）：

**A组**：墨西哥、意大利、新西兰、科特迪瓦  
**B组**：阿根廷、瑞典、哥伦比亚、委内瑞拉  
**C组**：德国、乌拉圭、喀麦隆、巴拉圭  
**D组**：法国、挪威、厄瓜多尔、洪都拉斯  
**E组**：西班牙、斯洛伐克、埃及、沙特  
**F组**：英格兰、波兰、突尼斯、约旦  
**G组**：巴西、荷兰、智利、印尼  
**H组**：葡萄牙、丹麦、美国、格鲁吉亚  
**I组**：比利时、瑞士、日本、卡塔尔  
**J组**：克罗地亚、塞内加尔、韩国、阿尔及利亚  
**K组**：摩洛哥、乌克兰、加拿大、澳大利亚  
**L组**：伊朗、匈牙利、尼日利亚、巴拿马  

> ⚠️ 以上为示例分组，请按 FIFA 官方最终抽签结果录入。
