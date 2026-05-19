# WC Analytics — Open-Source Football Probability Calibration Framework

> **For Research · Not a Betting Tool**

[![License: CC BY-NC-SA 4.0](https://img.shields.io/badge/License-CC_BY--NC--SA_4.0-lightgrey.svg)](https://creativecommons.org/licenses/by-nc-sa/4.0/)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115-green)](https://fastapi.tiangolo.com/)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen)](https://github.com/VariableLab/football/pulls)

🌐 [football.nett.to](https://football.nett.to)

---

## 🎯 One-Liner

WC Analytics is an **open-source football probability calibration research framework** for validating predictive models — not for providing betting advice.

🔬 Academic Use · 📊 Probability Output · 🔐 Pre-Match Snapshot Locking · 🌐 Fully Open-Source & Reproducible

---

## 📝 Description

WC Analytics is a **3-layer fusion football match probability modeling system** covering **31K+ historical matches** and **462 teams**. We open-source the code and data pipeline for academic validation of predictive models. All outputs are mathematical probabilities, pre-match locked for traceability, and never constitute betting advice.

**Core Principles**:
- ✅ Display only mathematically computed probabilities
- ✅ Pre-match snapshot locking for verifiability
- ❌ Never constitutes betting advice

---

## 🎯 Maker Story

We are a group of researchers focused on sports data analytics. We built this tool because we found many "prediction models" lack reproducibility and probability calibration. We hope to promote more rigorous football prediction research — if you're working on similar research, contributions are welcome!

---

## 📊 Current Status

| Metric | Value | Notes |
|--------|-------|-------|
| Total Matches | 31,402 | 46 leagues/tournaments |
| Finished | 31,238 | Includes 230 World Cup KO matches (1930-2022) |
| Teams | 462 | Auto-discovered + manual entry |
| Total Predictions | 157,030 | 5 play types fully covered |
| Jingcai Issues | 14 | Auto-synced from zgzcw |
| Odds Sources | Multi-channel | zgzcw + historical backfill |

---

## Architecture

```
Layer 1: Feature Generation
  ├── EloModel → Strength baseline
  ├── PoissonModel(Dixon-Coles) → Attack/defense matrix
  ├── MarketModel → Multi-source odds de-vig
  ├── AdjustmentModels → 8 correction factors
  ├── FormMarkovModel → Time-series form features
  └── H2HModel → Head-to-head features

Layer 2: Logistic Regression Fusion
  └── LogisticRegression(L1, L-BFGS-B, class_weight)
      → 43 features → SPF probability (30K+ matches trained)

Layer 3: Residual Neural Network
  └── ResidualNN (3-layer MLP)
      → Corrects LR systematic bias

Layer 4: Strategy Output
  ├── Platt Scaling calibration
  ├── EV calculation (model prob vs odds-implied)
  ├── 4-tier risk filtering
  └── Kelly stake optimization
```

---

## Performance

| Metric | LR Fusion | Target |
|--------|:-:|:-:|
| SPF Direction Accuracy | **56.6%** (backtest) | ≥ 55% |
| Brier Score | **~0.185** | ≤ 0.190 |
| Knockout Match Accuracy | **49.3%** | ≥ 45% |

---

## Quick Start

```bash
cd backend
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --reload --host 0.0.0.0 --port 8000
# Visit http://localhost:8000/static/index.html
```

---

## Project Structure

```
backend/
├── main.py                  # FastAPI application
├── models.py                # ORM models (12 tables)
├── prediction_engine.py     # 3-layer fusion engine
├── scheduler.py             # Task scheduler
├── zgzcw_jc_sync.py         # Jingcai match sync
├── health_daemon.py         # Self-check & repair
├── emergency_fix.py         # Diagnostic tool
│
├── features/                # Feature generation layer
├── fusion/                  # LR fusion layer
├── nn/                      # Neural networks
└── data/                    # Weights & audit data
```

---

## License

[CC BY-NC-SA 4.0](https://creativecommons.org/licenses/by-nc-sa/4.0/) — Non-commercial academic use, attribution required, share-alike.

---

## ⚠️ Disclaimer

This project is an academic research tool. Outputs are mathematically calibrated probabilities and do not constitute betting advice. Please comply with local laws and regulations, and maintain a rational perspective on sports competitions.

---

## Docs

Internal documentation moved to `docs/`:

| Document | Description |
|----------|-------------|
| [ARCHITECTURE_V2.md](docs/ARCHITECTURE_V2.md) | System architecture |
| [AUTOMATION.md](docs/AUTOMATION.md) | Automation pipeline |
| [ODDS_SETUP.md](docs/ODDS_SETUP.md) | Odds data configuration |
| [QUICKSTART.md](docs/QUICKSTART.md) | Getting started guide |
| [REMEDIATION_PLAN.md](docs/REMEDIATION_PLAN.md) | Remediation plan |
| [AUDIT_REPORT_20260519.md](docs/AUDIT_REPORT_20260519.md) | Audit report |
| [QUICK_FIX_GUIDE.md](docs/QUICK_FIX_GUIDE.md) | Quick fix guide |
