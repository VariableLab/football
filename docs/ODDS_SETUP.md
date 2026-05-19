# 数据接入与爬虫集成指南

## 当前状态

| 数据源 | 状态 | 说明 |
|--------|------|------|
| **football-data.co.uk** | 可用 | 免费CSV，含历史国际比赛赔率，适合回测 |
| **Odds API** | 需注册 | 实时赔率，$29/月或免费500次/月 |
| **BetExplorer** | 可用 | 免费网页爬虫，实时赔率 |
| **SoccerData (FBref/Understat/Elo)** | 已集成 | 1.7k stars 开源库，8大数据源 |
| **OddsHarvester (oddsportal)** | 已桥接 | 历史赔率时间序列，Playwright 驱动 |
| **竞彩官网** | 框架就绪 | 需补充爬虫解析逻辑 |
| **澳门彩票** | 框架就绪 | 需补充爬虫解析逻辑 |
| **香港马会** | 框架就绪 | 需补充爬虫解析逻辑 |

---

## 1. 第三方开源集成（新增）

### 1.1 SoccerData — 多源足球数据采集

- **项目地址**: https://github.com/probberechts/soccerdata
- **Stars**: 1.7k | **协议**: MIT | **版本**: v1.9.0
- **安装**: `pip install soccerdata>=1.9.0`

**支持的数据源（8个）**:

| 数据源 | 内容 | 本项目用途 |
|--------|------|-----------|
| **Club Elo** | 球队 Elo 等级分历史 | Elo 模型基线数据校准 |
| **FBref** | 详细球员/球队统计 | xG、传球、射门、阵容 |
| **Football-Data.co.uk** | 历史比赛+多公司赔率 | 回测数据集构建 |
| **Understat** | 射门级 xG 数据 | 泊松模型 λ 校准 |
| **WhoScored** | 球员评分+战术统计 | 球员状态修正 |
| **Sofascore** | 实时评分与事件 | 实时比赛监控 |
| **SoFIFA** | FIFA 游戏球员能力值 | 球员能力基线 |
| **ESPN** | 赛事赛程与比分 | 赛程同步 |

**用法**:
```python
from integrations.soccerdata_adapter import SoccerDataClient, SoccerDataSync

# 基础客户端
client = SoccerDataClient()

# 1. 获取 Club Elo 等级分（俱乐部/国家队）
ratings = client.fetch_elo_ratings("2022-12-18")
for r in ratings[:5]:
    print(f"{r.team_name}: {r.rating:.0f}")

# 2. 获取 FBref 世界杯球队统计
team_stats = client.fetch_fbref_team_stats("FIFA World Cup", "2022", "standard")
for s in team_stats:
    print(f"{s.team_name}: xG={s.xg_for:.1f} xGA={s.xg_against:.1f}")

# 3. 获取 FBref 球员统计
players = client.fetch_fbref_player_stats("FIFA World Cup", "2022")
for p in players[:10]:
    print(f"{p.player_name}: {p.minutes}min, {p.goals}G, xG={p.xg:.1f}")

# 4. 获取 Football-Data 历史比赛+赔率
matches = client.fetch_football_data_matches("W1", "2425")
for m in matches[:5]:
    print(f"{m.home_team} vs {m.away_team}: {m.odds_home}/{m.odds_draw}/{m.odds_away}")

# 5. 同步到数据库
from models import SessionLocal
db = SessionLocal()
sync = SoccerDataSync(db)
sync.sync_elo_ratings("2022-12-18")          # 更新 teams.elo
sync.sync_fbref_team_stats("FIFA World Cup", "2022")
sync.sync_football_data_odds("W1", "2425")    # 缓存历史赔率
```

**缓存策略**:
- SoccerData 自带本地缓存（`~/.soccerdata/`）
- 本项目额外缓存 JSON 到 `backend/.soccerdata_cache/`
- 回测时优先读取缓存，避免重复抓取

---

### 1.2 OddsHarvester — oddsportal 赔率历史采集

- **项目地址**: https://github.com/jordantete/OddsHarvester
- **Stars**: 169 | **协议**: MIT | **版本**: v0.2.0
- **安装**: `pip install git+https://github.com/jordantete/OddsHarvester.git`

**特点**:
- 数据来源: oddsportal.com（免费，无需 API key）
- 支持市场: 1x2（胜平负）、让球盘、大小球、双方进球
- 双模式: `upcoming`（即将开赛）/ `historic`（历史赛季）
- 输出格式: JSON / CSV / AWS S3

**用法**:
```python
from integrations.oddsharvester_bridge import OddsHarvesterCLI, OddsHarvesterSourceAdapter

# 直接 CLI 调用
cli = OddsHarvesterCLI()

# 抓取 upcoming 比赛赔率（平均赔率，速度快）
matches = cli.fetch_upcoming("soccer/world/world-cup", preview_only=True)

# 抓取历史赛季完整赔率（含各家博彩公司）
historic = cli.fetch_historic("soccer/world/world-cup", "2022", preview_only=False)

# 查找特定比赛
adapter = OddsHarvesterSourceAdapter()
match = adapter.find_match_odds(
    "soccer/world/world-cup", "2022",
    "Argentina", "France"
)
if match:
    print(f"Final odds: {match.odds_home}/{match.odds_draw}/{match.odds_away}")
```

**推荐部署方式**:
- **开发/测试**: 本地 pip 安装，subprocess 调用
- **生产**: 独立 Docker 容器定时抓取，结果写入共享卷，本项目读取 JSON
- 避免在 FastAPI 主进程内运行 Playwright（内存开销大）

```bash
# Docker 独立运行示例
docker run --rm -v $(pwd)/oddsharvester_output:/output oddsharvester \
    historic --sport soccer --league soccer/world/world-cup --season 2022 \
    --output /output/wc2022.json --format json
```

---

## 2. 自有数据源

### 2.1 football-data.co.uk（立即可用）

**用途**: 获取历史国际比赛（含世界杯预选赛、欧洲杯）的赔率和比分，用于 **模型回测**

**用法**:
```python
from odds_collector import FootballDataSource

fd = FootballDataSource()
data = fd.download_all()  # 下载全部国际比赛CSV

# 查找特定比赛赔率
odds = fd.find_match_odds(data, "Argentina", "France", "2022-12-18")
# → {'bet365_home': 1.95, 'bet365_draw': 3.40, 'bet365_away': 4.20, ...}
```

**字段映射**:
- `B365H/D/A` = Bet365 主胜/平/客胜
- `PSH/D/A` = Pinnacle（ sharper 赔率，更反映真实概率）
- `WHH/D/A` = William Hill

**建议**: 用 Pinnacle 赔率作为市场基准（抽水最少，最接近真实概率）。

---

### 2.2 Odds API（需注册）

**用途**: 实时获取世界杯比赛的最新赔率（赛前72h内持续更新）

### 注册步骤

1. 访问 https://the-odds-api.com/
2. 注册账号，获取免费 API Key
3. 免费套餐：500 requests/month（适合测试）
4. 付费套餐：$29/month，10,000 requests（世界杯期间足够）

### 配置

```bash
# 编辑 backend/.env
ODDS_API_KEY=your-api-key-here
```

### 用法

```python
from odds_collector import OddsApiSource

api = OddsApiSource()
odds = api.get_odds(sport="soccer_fifa_world_cup", regions="eu")

# 返回结构：多场比赛 × 多博彩公司
for event in odds:
    print(event["home_team"], "vs", event["away_team"])
    for bm in event["bookmakers"]:
        print(" ", bm["title"], bm["markets"][0]["outcomes"])
```

**支持的 regions**:
- `eu` — 欧洲赔率（Bet365, Pinnacle, Unibet 等）
- `us` — 美国赔率
- `uk` — 英国赔率
- `au` — 澳洲赔率

**建议**: 世界杯期间用 `eu` 获取欧洲主流博彩公司赔率，和你的竞彩赔率做对比分析。

---

### 2.3 BetExplorer 网页爬虫（可用）

**用途**: 免费实时赔率，无需 API key

```python
from odds_collector import BetExplorerSource

be = BetExplorerSource()
snap = be.fetch(match)  # 返回 OddsSnapshot
```

---

## 3. 待补充爬虫（竞彩/澳门/港彩）

当前只提供了基础框架，需要根据实际情况补充解析逻辑。

### 为什么必须自己写爬虫

| 数据源 | 为什么重要 |
|--------|-----------|
| **竞彩** | 你的目标用户买的就是竞彩，竞彩赔率才是"市场基准" |
| **澳门** | 亚洲盘口风向标，让球数据必须从这里来 |
| **港彩** | 与欧洲赔率对比，发现定价差异 |

### 竞彩官网爬虫思路

```python
# odds_collector.py → JingcaiSource

class JingcaiSource(OddsSource):
    def fetch(self, match):
        # 1. 请求每日赛程页面
        resp = self.client.get("https://www.sporttery.cn/cn/daily_matches/")
        
        # 2. 解析 HTML（BeautifulSoup）
        soup = BeautifulSoup(resp.text, 'html.parser')
        rows = soup.find_all('tr', class_='match-row')
        
        # 3. 匹配队名（需维护 中文→英文 映射表）
        for row in rows:
            home_cn = row.find('td', class_='home').text.strip()
            away_cn = row.find('td', class_='away').text.strip()
            
            # 映射到数据库中的英文队名
            home_en = TEAM_NAME_MAP.get(home_cn)
            away_en = TEAM_NAME_MAP.get(away_cn)
            
            if home_en == match.home_team.name and away_en == match.away_team.name:
                odds_home = float(row.find('td', class_='odds-home').text)
                odds_draw = float(row.find('td', class_='odds-draw').text)
                odds_away = float(row.find('td', class_='odds-away').text)
                return OddsSnapshot(...)
```

**关键**: 维护 `TEAM_NAME_MAP` 中文映射表（竞彩用的队名和 FIFA 可能不同）。

### 反爬应对

```python
# 控制频率
import time
time.sleep(random.uniform(2, 5))  # 每次请求间隔2-5秒

# 轮换 User-Agent
UA_LIST = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64)...",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)...",
]
headers = {"User-Agent": random.choice(UA_LIST)}

# 使用代理（如果被IP封禁）
proxies = {"http": "http://proxy:port", "https": "http://proxy:port"}
```

---

## 4. 采集调度

已经在 `scheduler.py` 中配置：

```python
# 每15分钟自动运行
collect_odds_job()
  → 查询72h内即将开始的比赛
  → 调用 OddsCollector 采集全部数据源
  → 存储最新赔率到 Match.odds_home/draw/away
  → 检测异动（变化>10%告警）
  → 写入审计日志
```

启动后自动运行，无需人工干预。

---

## 5. 测试命令

```bash
cd /Users/liuxuran/Github/football/backend

# 测试 football-data
python3 -c "
from odds_collector import FootballDataSource
fd = FootballDataSource()
data = fd.download_all()
print(f'Downloaded {len(data)} rows')
odds = fd.find_match_odds(data, 'Argentina', 'France', '2022-12-18')
print(odds)
"

# 测试 Odds API（需先配置 API key）
python3 -c "
from odds_collector import OddsApiSource
api = OddsApiSource()
sports = api.get_sports()
print([s['key'] for s in sports if 'world' in s['key']])
"

# 测试 SoccerData 集成
python3 -m integrations.soccerdata_adapter --source fbref --league "FIFA World Cup" --season 2022

# 测试 OddsHarvester 桥接（需先安装 oddsharvester CLI）
python3 -m integrations.oddsharvester_bridge --mode historic --league soccer/world/world-cup --season 2022
```

---

## 6. 数据源选型决策树

```
需要历史数据做回测？
  ├─ 是 → 需要多公司赔率？
  │         ├─ 是 → SoccerData (Football-Data) + OddsHarvester
  │         └─ 否 → football-data.co.uk CSV
  └─ 否 → 需要实时赔率？
            ├─ 是 → 有预算？
            │         ├─ 是 → Odds API ($29/月)
            │         └─ 否 → BetExplorer 爬虫 / OddsHarvester upcoming
            └─ 否 → 需要球员/球队统计？
                      ├─ 是 → SoccerData (FBref / Understat / WhoScored)
                      └─ 否 → 内部数据库
```

---

## 下一步行动

1. **立即**: 运行 football-data 测试，确认能下载历史数据
2. **今天**: `pip install soccerdata>=1.9.0`，测试 FBref 世界杯数据抓取
3. **今天**: 注册 Odds API，把 key 填入 `.env`
4. **本周**: 写竞彩官网爬虫（最重要！用户买的是竞彩）
5. **可选**: 
   - 部署 OddsHarvester Docker 抓取 2022 世界杯历史赔率
   - 用 SoccerData Club Elo 数据校准本项目 Elo 基线
