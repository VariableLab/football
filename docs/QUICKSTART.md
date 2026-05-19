# 快速启动指南

> 5分钟内在本地跑通完整系统

---

## 1. 安装依赖

```bash
cd /Users/liuxuran/Github/football/backend

python3 -m venv venv
source venv/bin/activate

pip install -r requirements.txt
```

---

## 2. 配置环境变量

```bash
cp .env.example .env
```

编辑 `.env`：
```
SECRET_KEY=your-secret-key-here-32-chars-long!!
ADMIN_API_KEY=your-admin-key-change-me
```

> `ODDS_API_KEY` 和 `STRIPE_*` 可先留空，不影响本地运行

---

## 3. 填充测试数据

```bash
python3 seed.py
```

输出示例：
```
✅ Created 6 teams
✅ Created 3 matches
✅ Created predictions for 3 matches
✅ Created test user: test@example.com / password: test123
✅ Created 10 tournament license keys
```

---

## 4. 启动服务

```bash
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

---

## 5. 访问页面

打开浏览器：

```
http://localhost:8000/static/index.html
```

你会看到：
- 3 场测试比赛卡片
- 阿根廷 vs 巴西（未开赛，策略锁定）
- 德国 vs 法国（未开赛，策略锁定）
- 英格兰 vs 爱尔兰（已结束，策略开放）

---

## 6. 测试完整流程

### 6.1 登录

点击右上角 **"登录 / 注册"**
- 邮箱：`test@example.com`
- 密码：`test123`

### 6.2 查看免费数据

未登录也能看到：
- 比赛对阵、时间、赔率
- 已结束比赛的预测结果对比

### 6.3 解锁策略

点击锁定比赛的 **"¥9.9 解锁本场"** → 输入卡密

用管理员 API 查看可用卡密：
```bash
curl -H "X-API-Key: your-admin-key" \
  http://localhost:8000/api/admin/licenses?used=false
```

复制一个未使用的卡密，粘贴到激活框，点击激活。

### 6.4 查看策略

激活后刷新页面，点击任意比赛卡片：
- 5 个 Tab：胜平负 / 让球 / 比分 / 总进球 / 半全场
- 每个选项显示概率条 + 赔率 + EV值

---

## 7. 管理后台测试

```bash
# 查看系统统计
curl -H "X-API-Key: your-admin-key" \
  http://localhost:8000/api/admin/dashboard

# 查看球队列表
curl -H "X-API-Key: your-admin-key" \
  http://localhost:8000/api/admin/teams

# 录入比赛结果
curl -H "X-API-Key: your-admin-key" -X PATCH \
  http://localhost:8000/api/admin/matches/1/result \
  -H "Content-Type: application/json" \
  -d '{"actual_home_goals":3,"actual_away_goals":1}'
```

---

## 8. 预测引擎测试

```bash
python3 prediction_engine.py
```

输出：
- 单场比赛完整预测（6种玩法）
- 20场模拟回测结果
- 最优权重 + 准确率指标

---

## 9. 部署到生产

```bash
# 把代码推送到服务器
rsync -avz --exclude='venv' --exclude='__pycache__' \
  /Users/liuxuran/Github/football/ \
  ubuntu@your-server-ip:/home/ubuntu/football/

# 在服务器上执行
ssh ubuntu@your-server-ip
cd /home/ubuntu/football/backend
chmod +x deploy.sh
sudo ./deploy.sh
```

部署后访问 `http://your-server-ip/`

---

## 常见问题

**Q: 页面显示"数据加载失败"？**
A: 检查后端是否启动：`curl http://localhost:8000/api/health`

**Q: 卡密激活失败？**
A: 确保卡密未被使用：`curl -H "X-API-Key: your-admin-key" http://localhost:8000/api/admin/licenses`

**Q: 如何清空数据重新开始？**
A: `rm database.sqlite && python3 seed.py`

**Q: 赔率显示为"-"？**
A: 需要配置 Odds API key 或运行爬虫。本地测试可忽略。

---

## 下一步

1. 接入真实赔率 → 阅读 [ODDS_SETUP.md](ODDS_SETUP.md)
2. 自动化运维 → 阅读 [AUTOMATION.md](AUTOMATION.md)
3. 管理操作 → 阅读 [OPENCLAW_MANUAL.md](OPENCLAW_MANUAL.md)
