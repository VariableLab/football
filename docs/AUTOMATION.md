# 自动化实现方案

> 目标：将人工操作降到最低，系统7×24小时自动运行

---

## 1. 自动化全景图

```
┌─────────────────────────────────────────────────────────────────────┐
│                        自动化调度中心                                │
│                    （APScheduler + systemd）                          │
├─────────────┬─────────────┬─────────────┬─────────────┬─────────────┤
│  赔率采集    │  预测锁定    │  比赛监控    │  结果同步    │  数据备份    │
│  每15分钟    │  每小时      │  每分钟      │  每5分钟     │  每日凌晨    │
└──────┬──────┴──────┬──────┴──────┬──────┴──────┬──────┴──────┬──────┘
       │             │             │             │             │
       ▼             ▼             ▼             ▼             ▼
   ┌───────┐    ┌───────┐    ┌───────┐    ┌───────┐    ┌──────────┐
   │ 竞彩   │    │ Elo   │    │ 检测   │    │ FIFA  │    │ SQLite   │
   │ 澳门   │───→│ 泊松  │    │ 开球   │    │ Flash │    │ 备份到   │
   │ 港彩   │    │ 融合  │    │ 结束   │    │ Score │    │ backup/  │
   └───────┘    └───────┘    └───────┘    └───────┘    └──────────┘
       │             │             │             │
       │             │             │             │
       ▼             ▼             ▼             ▼
   ┌───────────────────────────────────────────────────────────────┐
   │                        数据库 + API                            │
   │   SQLite (比赛/预测/赔率/用户)  ←→  FastAPI  ←→  前端看板      │
   └───────────────────────────────────────────────────────────────┘
```

---

## 2. 六大自动化模块

### 2.1 赔率自动采集

**触发频率**：赛前72h每15分钟，赛前6h每5分钟，赛前1h每1分钟

**实现方式**：
```python
# scheduler.py → collect_odds_job()

def collect_odds_job():
    matches = get_upcoming_matches(hours=72)
    for match in matches:
        # 三源并行采集
        eu_odds = fetch_bet365(match)      # 欧洲赔率
        asia_odds = fetch_macau(match)      # 澳门盘口
        hk_odds = fetch_hkjc(match)         # 香港马会
        
        # 存储 + 异动检测
        store_odds(match.id, eu_odds, asia_odds, hk_odds)
        detect_odds_anomaly(match.id, threshold=0.10)  # 10%异动告警
```

**数据源接入**：

| 来源 | 方式 | 稳定性 | 成本 |
|------|------|--------|------|
| **football-data.co.uk** | 免费CSV下载 | ⭐⭐⭐ | 免费 |
| **Odds API** | REST API | ⭐⭐⭐⭐ | ~$30/月 |
| **竞彩官网** | 爬虫 | ⭐⭐ | 免费，需反爬 |
| **澳门彩票** | 爬虫 | ⭐⭐ | 免费，需反爬 |
| **香港马会** | 爬虫 | ⭐⭐ | 免费，需反爬 |

**建议**：先用 football-data.co.uk + Odds API 做稳定层，澳门/港彩爬虫作为补充。

---

### 2.2 赛前预测自动锁定

**触发频率**：赛前48h自动运行，赛前2h强制最终锁定

**实现方式**：
```python
def lock_predictions_job():
    matches = get_matches_starting_within(hours=48)
    
    for match in matches:
        if already_locked(match.id):
            continue
        
        # 1. 采集最新数据
        team_stats = get_team_stats(match.home_team_id, match.away_team_id)
        odds = get_latest_odds(match.id)
        injuries = get_injury_report(match.id)
        
        # 2. 运行4模型
        p_elo = elo_model(team_stats)
        p_poisson = poisson_model(team_stats)
        p_player = player_adjustment(team_stats, injuries)
        p_market = market_implied(odds)
        
        # 3. 融合层
        final = ensemble_fusion(p_elo, p_poisson, p_player, p_market)
        
        # 4. 计算全部6种玩法
        predictions = {
            "spf": final["spf"],
            "rq": calculate_rq(final, handicap=-1),
            "score": poisson_bivariate(team_stats),
            "goals": total_goals_distribution(team_stats),
            "half": half_time_model(team_stats)
        }
        
        # 5. 锁定快照（不可修改）
        for play_type, probs in predictions.items():
            create_prediction_snapshot(match.id, play_type, probs)
        
        # 6. 标记为UPCOMING
        update_match_status(match.id, "upcoming")
```

**关键规则**：
- 锁定后不可修改（用于赛后验证）
- 赛前2h内不再更新（防止临场异动干扰）
- 快照包含输入数据哈希，可追溯

---

### 2.3 比赛状态自动监控

**触发频率**：每分钟

**实现方式**：
```python
def match_monitor_job():
    now = datetime.utcnow()
    
    # 检测即将开始
    starting = get_matches_between(now, now + timedelta(minutes=5))
    for match in starting:
        if match.status == "upcoming":
            update_status(match.id, "live")
            send_notification(f"{match.home} vs {match.away} 开球")
    
    # 检测可能已结束（开球后>105分钟）
    likely_ended = get_live_matches_older_than(minutes=105)
    for match in likely_ended:
        # 触发结果同步任务
        trigger_sync_result(match.id)
        # 发告警提醒管理员确认
        send_admin_alert(f"Match {match.match_code} likely ended, awaiting result")
```

---

### 2.4 赛后结果自动同步

**触发频率**：每5分钟（对LIVE状态的比赛）

**实现方式**：
```python
def sync_results_job():
    live_matches = get_live_matches()
    
    for match in live_matches:
        # 从多个源尝试获取结果
        result = None
        for source in [fifa_api, flashscore_api, espn_api]:
            result = source.get_result(match.match_code)
            if result and result["status"] == "finished":
                break
        
        if result:
            # 自动录入结果
            update_match_result(
                match_id=match.id,
                home_goals=result["home"],
                away_goals=result["away"]
            )
            
            # 自动计算准确率
            calculate_accuracy(match.id)
            
            # 通知所有用户结果已开放
            broadcast_open(match.id)
```

**数据源优先级**：
1. **FIFA官方API**（最权威，但可能有延迟）
2. **FlashScore API**（最快，但需验证）
3. **ESPN API**（备用）

**容错机制**：
- 三源结果不一致 → 标记"待人工确认"，不发通知
- 三源一致 → 自动录入，无需人工

---

### 2.5 支付与卡密自动开通

**用户自助流程**：
```
用户付款 → 收到卡密（WC26-XXXX-XXXX-XXXX-XXXX）
    ↓
打开网站 → 登录账号 → 点击"激活" → 输入卡密
    ↓
系统验证卡密有效性 → 自动开通权限 → 立即生效
    ↓
用户可查看全部赛前策略
```

**卡密系统自动化**：
```python
# 批量生成（管理员/OpenClaw调用）
POST /api/admin/licenses/generate
{ "license_type": "tournament", "count": 100 }
→ 返回100个未使用卡密

# 用户自助兑换（无需人工）
POST /api/license/redeem
{ "key": "WC26-AB3D-9F2A-KL7M-PQ8R" }
→ 验证通过 → 更新 user.is_paid = True → 返回成功
```

**Stripe自动支付（可选升级）**：
```
用户点击"购买" → Stripe Checkout → 付款成功
    ↓
Stripe Webhook → 服务器接收 payment_intent.succeeded
    ↓
自动生成卡密 → 发送到用户邮箱
    ↓
用户用卡密兑换（同上）
```

---

### 2.6 数据自动备份

**触发频率**：每日凌晨3:00

**实现方式**：
```python
def backup_database_job():
    # 1. 复制数据库文件
    shutil.copy2("database.sqlite", f"backup/db_{timestamp}.sqlite")
    
    # 2. 压缩（节省空间）
    subprocess.run(["gzip", backup_path])
    
    # 3. 上传到云存储（可选）
    upload_to_s3(backup_path + ".gz")
    
    # 4. 清理旧备份（保留7天）
    remove_old_backups(days=7)
```

---

## 3. 需要人工介入的环节

| 环节 | 自动化程度 | 人工操作 | 频率 |
|------|-----------|---------|------|
| **球队/比赛录入** | 半自动 | 赛程公布后批量录入（用Admin API） | 赛前1次 |
| **结果录入** | 90%自动 | 三源不一致时需人工确认 | 每场 |
| **卡密销售** | 半自动 | 导出卡密分发到销售渠道 | 按需 |
| **模型调参** | 手动 | 小组赛阶段每天根据回测调整 | 每日 |
| **服务器运维** | 自动 | 仅故障时介入 | 不定期 |

---

## 4. 启动自动化

### 4.1 启动后端 + 调度器

```bash
cd /Users/liuxuran/Github/football/backend

# 安装依赖
pip install -r requirements.txt

# 创建环境变量
cp .env.example .env
# 编辑 .env，填写 ADMIN_API_KEY 和 SECRET_KEY

# 启动（开发模式）
uvicorn main:app --reload --host 0.0.0.0 --port 8000

# 启动（生产模式，systemd）
systemctl start wc-analytics
```

### 4.2 systemd 服务配置（生产）

```ini
# /etc/systemd/system/wc-analytics.service
[Unit]
Description=WC Analytics Backend
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/football/backend
Environment=PYTHONPATH=/home/ubuntu/football/backend
ExecStart=/home/ubuntu/.local/bin/uvicorn main:app --host 0.0.0.0 --port 8000
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

---

## 5. 监控与告警

### 5.1 系统健康检查

```bash
# 每分钟检查一次
curl https://your-domain.com/api/health
→ {"status": "ok", "version": "0.1.0"}
```

### 5.2 关键告警

| 告警条件 | 通知方式 | 处理 |
|---------|---------|------|
| 服务器宕机 | 邮件/短信 | 自动重启systemd |
| 赔率采集失败>3次 | 企业微信 | 检查数据源 |
| 比赛结束2h未录入结果 | 邮件+站内通知 | 人工确认 |
| 数据库备份失败 | 邮件 | 检查磁盘空间 |
| 异常高并发（>1000/min） | 邮件 | 检查是否被攻击 |

---

## 6. 下一步行动清单

1. **接入赔率数据源** — 注册 Odds API 或写竞彩爬虫
2. **接入比赛结果源** — 调研 FlashScore / FIFA API 接入方式
3. **部署测试** — 把后端部署到GCP，跑通全链路
4. **写预测引擎** — Elo + 泊松模型Python实现
5. **接入Stripe** — 如果需要自动支付（可选）

需要我先帮你写哪个模块的代码？
