# WC Analytics — Architecture & Operations Guide

> Last updated: 2026-05-20

## Table of Contents

1. [Project Overview](#1-project-overview)
2. [Tech Stack](#2-tech-stack)
3. [Directory Structure](#3-directory-structure)
4. [Architecture Design](#4-architecture-design)
5. [Data Model](#5-data-model)
6. [Frontend Architecture](#6-frontend-architecture)
7. [Backend API & Module Breakdown](#7-backend-api--module-breakdown)
8. [Scheduled Tasks](#8-scheduled-tasks)
9. [Data Collection](#9-data-collection)
10. [Machine Learning Pipeline](#10-machine-learning-pipeline)
11. [Server Deployment & Operations](#11-server-deployment--operations)
12. [Daily Operations](#12-daily-operations)
13. [Development Workflow](#13-development-workflow)
14. [Future Optimization Roadmap](#14-future-optimization-roadmap)

---

## 1. Project Overview

**Repository**: [https://github.com/VariableLab/football](https://github.com/VariableLab/football)
**Live Site**: [https://football.nett.to](https://football.nett.to)
**License**: CC BY-NC-SA 4.0 (Academic Research)

### One-Liner

WC Analytics is an **open-source football probability calibration research framework** covering **31K+ historical matches** and **462 teams** with a 3-layer fusion probability modeling system. All outputs are mathematical probabilities, pre-match locked for traceability, and never constitute betting advice.

### Core Features

- **Jingcai (Chinese lottery) match display** — Show all on-sale matches by issue with SPF/handicap/score/goals/half odds
- **Model predictions** — Primary model + sub-models (half-time/score/handicap) fused probabilities
- **Tiered strategy** — Kelly sizing + risk tiers (conservative/balanced/aggressive/speculative)
- **Smart combo recommendations** — EV-based optimal parlay combinations
- **Live odds** — Real-time SSE push + live hedge alerts
- **Model validation dashboard** — Hit rate comparison with actual results, calibration curves
- **AI analysis assistant** — Natural language match analysis via openai-compatible API
- **Prediction reports** — Post-match reviews per issue
- **User system** — Registration/login/license key redemption
- **i18n** — 6 languages (Chinese/English/French/Spanish/German/Italian)

---

## 2. Tech Stack

| Layer | Technology | Purpose |
|-------|-----------|---------|
| Backend | **FastAPI** (Python 3.10+) | REST API + SSE + static file serving |
| Database | **SQLite** (WAL mode) | Single-file database for everything |
| ORM | **SQLAlchemy 2.0** | Data models and queries |
| Scheduler | **APScheduler** | Timed data collection/refresh/self-heal |
| Frontend | Vanilla **HTML/CSS/JS** | SPA-style, Tailwind CSS + custom theme |
| Proxy | **Nginx** | HTTPS termination + API reverse proxy |
| Service | **systemd** | Process management + auto-restart |
| ML Models | **PyTorch** + **scipy** | BetNN + sub-models + probability calibration |

---

## 3. Directory Structure

```
football/
├── backend/                          # Python backend
│   ├── main.py                       # FastAPI entry point / routes
│   ├── models.py                     # SQLAlchemy ORM models
│   ├── schemas.py                    # Pydantic request/response models
│   ├── config.py                     # Configuration management (env vars)
│   ├── auth.py                       # JWT authentication
│   ├── scheduler.py                  # APScheduler task center
│   ├── deploy.sh                     # Initial deployment script
│   ├── sync_jc_to_server.py          # Local → server data sync tool
│   ├── requirements.txt              # Python dependencies
│   ├── .env.example                  # Environment variable template
│   │
│   ├── prediction_engine.py          # Core prediction engine
│   ├── calibrator.py                 # Probability calibration (Platt/Isotonic)
│   ├── fusion_strategy.py            # 3-layer fusion strategy
│   ├── strategy_pipeline.py          # Kelly sizing pipeline
│   ├── tiered_strategy.py            # Tiered strategy analysis
│   ├── optimal_combo.py              # Combo/parlay recommendation
│   ├── position_sizer.py             # Position size calculator
│   ├── edge_calculator.py            # EV/Edge calculation
│   ├── risk_manager.py               # Risk control
│   │
│   ├── health_daemon.py              # Self-healing daemon
│   ├── alert_manager.py              # Alert management
│   ├── model_audit.py                # Model audit
│   ├── validation_engine.py          # Validation engine
│   ├── data_cleaner.py               # Data cleaning
│   │
│   ├── odds_collector.py             # Odds collector
│   ├── odds_tracker.py               # Odds movement tracker
│   ├── live_odds_feed.py             # Live odds push
│   ├── live_hedge_engine.py          # Live hedge engine
│   ├── hedge_engine.py               # Arbitrage scanner
│   ├── zgzcw_source.py               # Chinese odds portal scraper
│   ├── wubaibai_source.py            # 500.com odds scraper
│   │
│   ├── bet_nn.py                     # Prediction neural network
│   ├── sub_model_halftime.py         # Half-time sub-model
│   ├── sub_model_score.py            # Score prediction sub-model
│   ├── sub_model_handicap.py         # Handicap sub-model
│   ├── residual_nn.py                # Residual network
│   ├── xg_estimator.py               # xG estimator
│   │
│   ├── scheduler.py                  # Task scheduler
│   ├── strategy_monitor.py           # Strategy drift monitoring
│   ├── param_optimizer.py            # Parameter optimization
│   ├── weight_learner.py             # Weight learning
│   │
│   ├── jingcai_predictor.py          # Jingcai issue predictor
│   ├── form_collector.py             # Team form data collection
│   ├── injury_sync.py                # Injury data
│   ├── result_sync.py                # Match result sync
│   ├── license_manager.py            # License key management
│   ├── sse.py                        # SSE event push
│   └── admin.py                      # Admin routes
│
├── static/                           # Frontend static files
│   ├── index.html                    # SPA entry point
│   ├── app.js                        # Main app logic (1500+ lines)
│   ├── i18n.js                       # Internationalization engine
│   ├── api_client.js                 # API client wrapper
│   ├── input.css / tailwind.css      # Tailwind styles
│   ├── legal.html                    # Legal/privacy page
│   ├── locales/                      # 6 language translation files
│   │   ├── zh.json / en.json / fr.json / es.json / de.json / it.json
│   └── src/                          # Tailwind source
│
├── docs/                             # Documentation
├── screenshots/                      # Product Hunt screenshots
├── tests/                            # Tests
│
├── demo.mp4                          # 30s promo video (with voiceover)
├── demo_preview.gif                  # 5s GIF preview
└── README.md / README_ZH.md          # English & Chinese READMEs
```

---

## 4. Architecture Design

### Architecture Overview

```
┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│   Browser    │────▶│    Nginx     │────▶│   FastAPI    │
│  (SPA SPA)   │◀────│  (HTTPS +    │◀────│  Uvicorn ①   │
│              │     │  reverse     │     │   :8000      │
└──────────────┘     │  proxy)      │     └──────┬───────┘
                     └──────────────┘            │
                    ┌─────────────────────────────┤
                    │              │              │
               ┌────▼────┐   ┌────▼────┐   ┌────▼────┐
               │ SQLite  │   │ APSched │   │PyTorch  │
               │ WAL mode│   │ Sched.  │   │ Models  │
               └─────────┘   └─────────┘   └─────────┘
                    │
                    │  External Data Sources
                    ├── zgzcw.com (Chinese 百家 odds, free)
                    ├── 500.com (Chinese bookmaker odds, free)
                    ├── football-data.org (results/standings, API)
                    ├── the-odds-api.com (international odds, paid quota)
                    └── deepstock.zone.id (AI analysis, API)
```

> ① Single worker deployment. Multi-worker requires Redis migration for live-odds global state.

### Key Design Decisions

| Decision | Rationale |
|----------|-----------|
| SQLite (not PostgreSQL) | Single-server deployment, no high-concurrency write needs, zero ops overhead |
| Vanilla JS (not React/Vue) | Focused functionality, no complex state management, minimal build steps |
| Embedded Chinese i18n | Avoids XHR async loading leading to missing translations on first render |
| Single worker | Live-odds SSE uses module-level globals; multi-worker needs Redis |
| Nginx proxy + Let's Encrypt | Lightweight HTTPS termination, easy management |

---

## 5. Data Model

Full definitions in `backend/models.py` (546 lines). Core tables:

### Core Tables

| Table | Purpose | Key Fields |
|-------|---------|------------|
| `matches` | Match main table | `home_team_id, away_team_id, kickoff_at, odds_home/draw/away, status, match_type, actual_outcome, actual_goals` |
| `teams` | Teams | `name, flag, fifa_rank, elo, form_factor` |
| `predictions` | Prediction records | `match_id, play_type, probabilities(JSON), model_version, locked_at` |
| `users` | Users | `email, password_hash, is_paid, paid_until` |
| `jingcai_issues` | Lottery issues | `issue_id, issue_type, status(on_sale/drawn/verified), sale_start/end` |
| `jingcai_issue_matches` | Issue↔Match join | `issue_id, match_id, sequence, handicap, rq_odds/score_odds/goals_odds/half_odds(JSON)` |
| `odds_history` | Odds time-series | `match_id, odds_home/draw/away, source, recorded_at` |
| `match_bookmaker_odds` | Multi-bookmaker odds | `match_id, bookmaker, odds_home/draw/away, updated_at` |
| `feedback` | User messages | `user_id, category, content, likes` |
| `license_keys` | License keys | `key, license_type, is_used, used_by` |
| `user_settings` | User preferences | `risk_tier, default_play_type, show_ev` |

### Data Flow

```
[zgzcw.com / 500.com] ──scrape──▶ [matches + jingcai_issue_matches]
                                          │
                                    [prediction_engine]
                                          │
                                    [predictions table]
                                          │
                    ┌───────────────────────┤
                    │                       │
              [strategy_pipeline]    [optimal_combo]
              (Kelly sizing)         (Smart parlay)
```

---

## 6. Frontend Architecture

### File Structure

```
static/
├── index.html        # 266 lines, SPA entry
├── app.js            # 1504 lines, main app logic
├── i18n.js           # i18n engine (embedded 228 Chinese translations)
├── api_client.js     # API call wrapper
├── locales/          # 6 language JSON files (228 keys each)
└── tailwind.css      # Compiled Tailwind styles
```

### SPA Tabs (no URL routing)

| Tab | Function |
|-----|----------|
| Matches | View match list by issue, model predictions, strategy analysis |
| Validation | Model prediction accuracy dashboard vs actual results |
| Reports | Post-match review reports per issue |
| AI Analysis | Large language model-powered match analysis |
| Feedback | User message board |

### Internationalization

- `i18n.js` embeds `cache['zh']` with all Chinese translations — available immediately on script load
- Other languages loaded asynchronously via XHR from `/static/locales/{lang}.json`
- `I18n.t(key, ...args)` — `%d` for number placeholder, `%s` for string placeholder
- `I18n.init()` — auto-executes, detects browser language / localStorage
- `data-i18n` attribute — auto-translates static HTML elements
- `i18n:change` event — triggers frontend re-render on language switch

### Theme

Warm-elegant style, dark-mode primary, using Tailwind CSS custom palette (`charcoal` / `beige` / `cream` / `warm-gray`).

---

## 7. Backend API & Module Breakdown

### Route Modules

| Prefix | File | Function |
|--------|------|----------|
| `/` | `main.py` | Homepage file serving |
| `/api/auth/*` | `main.py:178-218` | User register/login/profile |
| `/api/matches/*` | `main.py:263-545` | Match list/detail/strategy/odds movement |
| `/api/jingcai/*` | `main.py:1037-1262` | Jingcai issue CRUD/predict/results/verify/report/combo |
| `/api/validation/*` | `main.py:816-838` | Model validation/calibration curve/play-type breakdown |
| `/api/feedback/*` | `main.py:1460-1561` | Feedback CRUD/like |
| `/api/live-odds/*` | `main.py:597-709` | Live odds SSE/polling/start/stop |
| `/api/live-hedge/*` | `main.py:715-810` | Live hedge/alerts/positions/compute |
| `/api/arbitrage` | `main.py:551-583` | Cross-bookmaker arbitrage scanner |
| `/api/health` | `main.py:1389-1448` | Health check + detailed report |
| `/api/settings/*` | `main.py:1567-1628` | User preferences |
| `/api/bet-nn/*` | `main.py:1634-1664` | BetNN status/inference/training |
| `/api/sub-models/*` | `main.py:1670-1743` | Sub-model (half-time/score/handicap) status/training |
| `/api/predictions/*` | `main.py:1715-1722` | Composite prediction report |
| `/api/strategy/*` | `main.py:1816-1920` | Strategy params/tiered analysis/optimization/drift monitor |
| `/api/sporttery/*` | `main.py:1749-1810` | sporttery.cn data sync |
| `/api/chat` | `main.py:2003-2063` | AI analysis (openai-compatible API) |
| `/api/admin/*` | `main.py:1304-1383` | Odds refresh/data quality audit/clean |
| `/api/license/*` | `main.py:223-239` | License key redemption |

### Core Module Details

#### 7.1 Prediction Engine (`prediction_engine.py`)

3-layer fusion strategy:
1. **Elo baseline** — Base probabilities from Elo rating system
2. **Feature model** — Gradient boosted model trained on historical team statistics
3. **Market calibration** — Platt/Isotonic calibration using market odds

#### 7.2 Strategy Pipeline (`strategy_pipeline.py`)

```
predictions → Kelly sizing → EV ranking → Risk tiering → Output optimal picks
```

- Kelly fraction adjusted by risk preference (conservative 0.25 → speculative 1.0)
- Detects positive-expectation bets where `edge > 0`
- Calculates VaR (95%) / CVaR (95%)

#### 7.3 Health Daemon (`health_daemon.py`)

Self-check items (every 15 minutes):
- Database integrity (SQLite self-check)
- Odds freshness (time since last collection)
- Scheduler job status (46 active tasks)
- Data completeness (match count / missing odds / missing team data)
- Model drift (direction accuracy vs 48% threshold)
- Rolling self-heal (triggers retraining when accuracy drops)

#### 7.4 Odds Collection (`odds_collector.py`)

Three-tier collection architecture:
- **Tier 1 (Free)** — zgzcw.com + 500.com Chinese bookmaker odds, 30-minute intervals
- **Tier 2 (Quota)** — the-odds-api.com, budget-managed to avoid overuse
- **Tier 3 (Fallback)** — Synthetic odds (based on Elo + historical matchups)

#### 7.5 Jingcai Issue (`jingcai_predictor.py`)

- `create_issue` — Create issue and link matches
- `predict_issue` — Generate predictions for an entire issue
- `record_draw_result` — Record official lottery results
- `verify_issue` — Validate model predictions vs actual results

#### 7.6 AI Chat (`main.py:2003-2063`)

Calls `deepstock.zone.id`'s OpenAI-compatible API (qwen3.5-397b-a17b model), injecting match context and prediction data to return natural language analysis.

---

## 8. Scheduled Tasks

All tasks registered in `backend/scheduler.py` (1423 lines).

### Task List

| Task | Interval | Function |
|------|----------|----------|
| `collect_zgzcw_job` | 30 min | Scrape Chinese odds portal |
| `collect_500_job` | 30 min | Scrape 500.com bookmaker odds |
| `collect_odds_tier1_job` | 2 hours | Tier 1 basic odds refresh |
| `collect_odds_tier1_secondary_job` | 2 hours | Secondary odds source refresh |
| `refresh_odds_job` | 1 hour | Comprehensive odds refresh |
| `predict_upcoming_job` | 1 hour | Generate predictions for upcoming matches |
| `self_heal_job` | 2 hours | Health self-check + self-heal |
| `model_audit_job` | 6 hours | Model audit |
| `train_bet_nn_job` | 12 hours | BetNN auto-training |
| `train_sub_models_job` | 12 hours | Sub-model auto-training |
| `drift_check_job` | 6 hours | Strategy drift detection |
| `param_optimize_job` | 24 hours | Parameter optimization |
| `validation_job` | 6 hours | Validation data update |
| `scrape_jingcai_job` | 2 hours | Jingcai data collection |
| `collect_form_job` | 6 hours | Team form data collection |
| `sync_results_job` | 6 hours | Match result sync |
| `auto_close_issues_job` | 1 hour | Auto-close expired issues |
| `sporttery_sync_job` | 6 hours | sporttery sync |

> Note: Most collection tasks depend on Chinese sources (zgzcw / 500.com / sporttery.cn) which are blocked from overseas servers. The production server relies on manual sync via `sync_jc_to_server.py`.

---

## 9. Data Collection

### Data Source Status

| Source | Status | Notes |
|--------|--------|-------|
| sporttery.cn | ❌ Dead | Returns HTTP 567 WAF block, permanently blocked |
| zgzcw.com | ✅ Working | Chinese bookmaker odds (37 companies including official/Macau/William Hill/bet365) |
| 500.com | ✅ Working | Supplementary bookmaker odds |
| the-odds-api.com | ✅ Working | International odds API, 500 free credits/month |
| football-data.org | ✅ Working | Match results/standings API |

### Sync Flow

**Local → Server data sync** (Manual):

```
Local (China IP) ─── sync_jc_to_server.py ───▶ Server (Oracle US)
    │                                                  │
    ├── Can scrape zgzcw                        Writes to football.db
    ├── Can scrape 500.com
    └── Cannot reach sporttery.cn
```

`sync_jc_to_server.py` workflow:
1. Runs `collect_zgzcw_odds()` locally to scrape Chinese bookmaker odds
2. Runs `collect_500_odds()` for supplementary odds
3. Runs `predict_upcoming()` to generate predictions
4. Uploads `football.db` to server via SCP
5. SSH into server and run `systemctl restart football.service`

---

## 10. Machine Learning Pipeline

### Primary Model Architecture

```
Input Features
  ├── Elo rating (home/away/diff)
  ├── Head-to-head history
  ├── Form factors (last 10 matches winrate/winstreak/losestreak)
  ├── FIFA ranking
  ├── Home/away factors
  └── Injury impact (injury_sync)
      │
      ▼
┌─────────────────────────────────────┐
│  Layer 1: Elo Baseline Model         │
│  P(Win|Draw|Loss) = f(Elo_diff)     │
├─────────────────────────────────────┤
│  Layer 2: Feature Model (PyTorch)    │
│  Gradient Boosted + Neural Net       │
├─────────────────────────────────────┤
│  Layer 3: Market Calibration         │
│  (Platt/Isotonic)                    │
│  Uses opening odds as prior          │
└─────────────────────────────────────┘
      │
      ▼
Calibrated Probabilities (SPF/RQ/Score/Goals/Half)
```

### Sub-Models

- **Half-time model** — Predicts half-time + full-time combined outcomes (9 possibilities)
- **Score model** — Predicts exact score distribution
- **Handicap model** — Handicap win/draw/loss probabilities

### BetNN (Prediction Neural Network)

PyTorch residual network for match-level prediction scoring and ranking within an issue. Inputs are primary model probabilities + odds + team features.

### Strategy Drift Monitoring

Detects model drift by comparing rolling window (recent matches) accuracy against a baseline snapshot. Triggers automatic retraining when drift is detected.

### Parameter Optimization

Grid search over Kelly coefficients, safety thresholds, VaR limits, etc., using historical backtest ROI as the optimization target.

---

## 11. Server Deployment & Operations

### Server Info

| Property | Value |
|----------|-------|
| Provider | Oracle Cloud (Free Tier VM) |
| IP | `129.146.124.72` |
| User | `ubuntu` |
| Domain | `football.nett.to` (Let's Encrypt SSL) |
| Timezone | UTC |

### Service Architecture

```
User ─── HTTPS :443 ───▶ Nginx ───▶ http://127.0.0.1:8000 ───▶ uvicorn (FastAPI)
                           │
                        football.nett.to SSL (Let's Encrypt)
```

### Nginx Configuration

Path: `/etc/nginx/sites-enabled/football.conf`

```nginx
server {
    server_name football.nett.to;
    listen 443 ssl;
    ssl_certificate /etc/letsencrypt/live/football.nett.to/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/football.nett.to/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 180s;
    }
}
```

### Systemd Service

Path: `/etc/systemd/system/football.service`

```ini
[Unit]
Description=WC Analytics Football Prediction API
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/Github/football/backend
Environment=PYTHONPATH=/home/ubuntu/Github/football/backend
ExecStart=/home/ubuntu/Github/football/backend/venv/bin/uvicorn main:app --host 127.0.0.1 --port 8000
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

> ⚠️ Note: The `deploy.sh` script uses service name `wc-analytics`, but the actual systemd service on the server is `football.service`.

### Environment Variables (Server)

Edit `/home/ubuntu/Github/football/backend/.env` via SSH:

| Variable | Description |
|----------|-------------|
| `SECRET_KEY` | JWT signing key (>= 32 chars) |
| `ADMIN_API_KEY` | Admin API access key |
| `ALLOWED_ORIGINS` | CORS whitelist |
| `DEBUG=false` | Must be false in production |
| `WC_ENV=production` | Enables production mode |

### Service Commands

```bash
# Status
sudo systemctl status football.service

# Restart
sudo systemctl restart football.service

# Logs
sudo journalctl -u football.service -n 50 --no-pager
sudo journalctl -u football.service -f                        # Live tail

# Start/Stop
sudo systemctl start football.service
sudo systemctl stop football.service
```

### Nginx Commands

```bash
# Test config
sudo nginx -t

# Reload
sudo nginx -s reload

# View access logs
sudo tail -f /var/log/nginx/access.log
```

---

## 12. Daily Operations

### 12.1 Data Sync (Daily)

Manually sync data from local machine (China IP) to server:

```bash
# Run locally
cd /Users/liuxuran/Github/football/backend
python3 sync_jc_to_server.py
```

**Note**: The server is overseas and cannot directly access Chinese data sources (zgzcw.com, 500.com, etc., which require a China IP). `sync_jc_to_server.py` runs collection and prediction locally, then pushes the database to the server via SCP.

### 12.2 Code Deployment

```bash
# 1. Develop and test locally
cd /Users/liuxuran/Github/football
git add -A && git commit -m "Describe changes"
git push origin master

# 2. SSH into server
ssh ubuntu@129.146.124.72

# 3. Pull and restart
cd /home/ubuntu/Github/football
git pull origin master
sudo systemctl restart football.service

# 4. Verify
curl -s http://127.0.0.1:8000/api/health | python3 -m json.tool
```

### 12.3 Health Check

```bash
# Basic health
curl http://football.nett.to/api/health

# Detailed self-check
curl http://football.nett.to/api/health/detailed

# Odds coverage
curl -H "X-Api-Key: $ADMIN_API_KEY" http://football.nett.to/api/admin/odds/status
```

### 12.4 Manual Trigger Operations

```bash
# Refresh all odds
curl -X POST -H "X-Api-Key: $ADMIN_API_KEY" http://football.nett.to/api/admin/odds/refresh

# Train neural network
curl -X POST -H "X-Api-Key: $ADMIN_API_KEY" http://football.nett.to/api/bet-nn/train

# Train all sub-models
curl -X POST -H "X-Api-Key: $ADMIN_API_KEY" http://football.nett.to/api/sub-models/train-all

# Trigger drift detection
curl -X POST -H "X-Api-Key: $ADMIN_API_KEY" http://football.nett.to/api/strategy/monitor/check

# Parameter optimization
curl -X POST -H "X-Api-Key: $ADMIN_API_KEY" http://football.nett.to/api/strategy/optimize

# Auto-close expired issues
curl -X POST -H "X-Api-Key: $ADMIN_API_KEY" http://football.nett.to/api/jingcai/issues/auto-close

# Data quality audit
curl -H "X-Api-Key: $ADMIN_API_KEY" http://football.nett.to/api/admin/data-quality

# Data cleaning (preview)
curl -X POST -H "X-Api-Key: $ADMIN_API_KEY" -H "Content-Type: application/json" \
  -d '{"dry_run": true}' http://football.nett.to/api/admin/data-clean
```

### 12.5 Manage Jingcai Issues

```bash
# Create issue
curl -X POST -H "X-Api-Key: $ADMIN_API_KEY" -H "Content-Type: application/json" \
  -d '{"issue_id":"JC20260520","issue_type":"jingcai","sale_start":"...","sale_end":"...","match_codes":[...]}' \
  http://football.nett.to/api/jingcai/issues

# Generate predictions
curl -X POST -H "X-Api-Key: $ADMIN_API_KEY" \
  http://football.nett.to/api/jingcai/issues/JC20260520/predict

# Record draw results
curl -X POST -H "X-Api-Key: $ADMIN_API_KEY" -H "Content-Type: application/json" \
  -d '{"results": [...], "prizes": {...}, "draw_at": "..."}' \
  http://football.nett.to/api/jingcai/issues/JC20260520/results

# Verify
curl -X POST -H "X-Api-Key: $ADMIN_API_KEY" \
  http://football.nett.to/api/jingcai/issues/JC20260520/verify
```

---

## 13. Development Workflow

### Local Development Setup

```bash
# 1. Clone
git clone https://github.com/VariableLab/football.git
cd football/backend

# 2. Create virtual environment
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# 3. Configure environment
cp .env.example .env
# Edit .env, fill in SECRET_KEY and ADMIN_API_KEY

# 4. Initialize database
python3 -c "from models import init_db; init_db(); print('OK')"

# 5. Start dev server
uvicorn main:app --reload --port 8000

# 6. Visit http://127.0.0.1:8000
```

### Iteration Flow

```
1. Identify issue / feature ──▶ 2. Local branch dev ──▶ 3. Test ──▶ 4. commit + push
                                                                          │
                                                                          ▼
5. SSH to server ──▶ 6. git pull ──▶ 7. systemctl restart
```

### Testing

```bash
cd backend
python3 -m pytest tests/ -v

# Or using pytest.ini config
cd ..
pytest
```

---

## 14. Future Optimization Roadmap

### High Priority

| Item | Description |
|------|-------------|
| **Multi-worker compatibility** | Migrate live-odds global state to Redis for horizontal scaling |
| **PostgreSQL migration** | SQLite performance degrades under heavy writes; migrate to PG for concurrency |
| **Auto CI/CD** | GitHub Actions for automated testing + server deployment |
| **Data source redundancy** | sporttery.cn is dead; find alternative Chinese lottery data source |

### Medium Priority

| Item | Description |
|------|-------------|
| **Frontend SSR** | Migrate to React/Vue for better DX and server-side rendering |
| **Swagger docs** | Enable API docs (/docs) in production |
| **WebSocket** | Upgrade SSE to WebSocket for bidirectional communication |
| **Model versioning** | Versioned storage of model parameters and weights |
| **Dockerization** | Containerized deployment to eliminate environment inconsistencies |

### Long-term

| Item | Description |
|------|-------------|
| **Automated data sync** | Cron job to replace manual `sync_jc_to_server.py` |
| **Stripe payment** | Production payment pipeline (already configured, not enabled) |
| **Mobile App** | iOS/Android client built on the current API |
| **Multi-league coverage** | Extend to Premier League/La Liga/Serie A etc. |
| **API marketplace** | Open API for third-party consumption |

---

## Appendix

### A. Key File Reference

| File | Lines | Function |
|------|-------|----------|
| `backend/main.py` | 2077 | FastAPI routes + business logic |
| `backend/models.py` | 546 | Data models (16 ORM classes) |
| `backend/scheduler.py` | 1423 | Scheduler (30+ timed tasks) |
| `backend/schemas.py` | ~100 | Pydantic response models |
| `backend/health_daemon.py` | ~500 | Self-healing daemon |
| `static/app.js` | 1504 | Frontend SPA logic |
| `static/i18n.js` | ~200 | Internationalization engine |

### B. SSH Configuration

```bash
# Server SSH key location
~/.ssh/server_key

# Connect
ssh -i ~/.ssh/server_key ubuntu@129.146.124.72
```

### C. Git Branch Strategy

- `master` — Production branch, directly deployed to server
- Feature development on local branches, squash-merge to master
- No develop/staging intermediate environments

### D. What Has Been Built (Status Summary)

- ✅ 6-language i18n (228 translation keys × 6 languages)
- ✅ English README.md + Chinese README_ZH.md
- ✅ 30s promo video (TTS voiceover + GIF preview)
- ✅ Product Hunt readiness (4 screenshots + OG meta)
- ✅ 3-layer model fusion (Elo + features + market calibration)
- ✅ Tiered strategy (4 risk profiles + Kelly sizing)
- ✅ Pre-match snapshot locking + post-match verification
- ✅ Self-healing daemon
- ✅ Chinese bookmaker odds scraping (zgzcw + 500.com)
- ✅ Jingcai issue lifecycle (create → predict → draw → verify → report)
- ✅ Smart combo/parlay recommendations (EV ranking)
- ✅ Live odds + SSE push
- ✅ AI analysis assistant (qwen3.5-397b)
- ✅ Let's Encrypt SSL + Nginx reverse proxy
- ✅ License key payment system
