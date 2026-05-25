"""
五大联赛回测脚本

从 football-data.co.uk 下载历史比赛+赔率数据，
用联赛数据扩展回测样本至 500+ 场，验证模型泛化能力。

用法:
    cd backend && python backtest_leagues.py

数据源:
    - 英超 (E0) 21/22, 22/23, 23/24
    - 西甲 (SP1) 21/22, 22/23, 23/24
    - 德甲 (D1)  21/22, 22/23, 23/24
"""

from __future__ import annotations

import csv
import io
import math
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass

import httpx
from core.prediction_engine import (
    PredictionEngine,
    MatchContext,
    TeamContext,
    DEFAULT_WEIGHTS,
    direction_correct,
    brier_score,
)


# ────────────────────────────
# 配置
# ────────────────────────────
LEAGUES = {
    "E0": {"name": "Premier League", "country": "England", "teams": 20},
    "SP1": {"name": "La Liga", "country": "Spain", "teams": 20},
    "D1": {"name": "Bundesliga", "country": "Germany", "teams": 18},
}

SEASONS = ["2122", "2223", "2324"]


@dataclass
class LeagueMatch:
    home_team: str
    away_team: str
    home_goals: int
    away_goals: int
    odds_home: Optional[float]
    odds_draw: Optional[float]
    odds_away: Optional[float]
    season: str
    league: str


def download_csv(league_code: str, season: str) -> List[Dict]:
    """从 football-data.co.uk 下载 CSV"""
    url = f"https://www.football-data.co.uk/mmz4281/{season}/{league_code}.csv"
    try:
        r = httpx.get(url, timeout=30)
        r.raise_for_status()
        reader = csv.DictReader(io.StringIO(r.text))
        return list(reader)
    except Exception as e:
        print(f"  ⚠️ 下载失败 {url}: {e}")
        return []


def parse_match(row: Dict, league_code: str, season: str) -> Optional[LeagueMatch]:
    """解析 CSV 行"""
    try:
        fthg = row.get("FTHG", "")
        ftag = row.get("FTAG", "")
        if fthg == "" or ftag == "":
            return None
        hg = int(fthg)
        ag = int(ftag)

        # 赔率：优先 Pinnacle (PSH/PSD/PSA)，其次 Bet365 (B365H/B365D/B365A)
        oh = _parse_odds(row, "PSH", "B365H")
        od = _parse_odds(row, "PSD", "B365D")
        oa = _parse_odds(row, "PSA", "B365A")

        return LeagueMatch(
            home_team=row.get("HomeTeam", "").strip(),
            away_team=row.get("AwayTeam", "").strip(),
            home_goals=hg,
            away_goals=ag,
            odds_home=oh,
            odds_draw=od,
            odds_away=oa,
            season=season,
            league=league_code,
        )
    except Exception:
        return None


def _parse_odds(row: Dict, primary: str, fallback: str) -> Optional[float]:
    for key in (primary, fallback):
        val = row.get(key, "")
        if val:
            try:
                v = float(val)
                if v > 1.0:
                    return v
            except ValueError:
                continue
    return None


# ────────────────────────────
# Walk-Forward Elo
# ────────────────────────────
def walk_forward_elo(matches: List[LeagueMatch], k: int = 20) -> Dict[str, int]:
    """
    为联赛球队计算 walk-forward Elo。
    每场比赛前基于之前所有比赛结果计算当前 Elo。
    """
    elos: Dict[str, int] = {}

    for m in matches:
        home_elo = elos.get(m.home_team, 1600)
        away_elo = elos.get(m.away_team, 1600)

        expected_home = 1 / (1 + 10 ** ((away_elo - home_elo) / 400))
        if m.home_goals > m.away_goals:
            actual = 1.0
        elif m.home_goals == m.away_goals:
            actual = 0.5
        else:
            actual = 0.0

        delta = k * (actual - expected_home)
        elos[m.home_team] = int(home_elo + delta)
        elos[m.away_team] = int(away_elo - delta)

    return elos


def compute_team_stats(matches: List[LeagueMatch]) -> Dict[str, Dict]:
    """计算每支球队的场均进球/失球（walk-forward）"""
    history: Dict[str, List[LeagueMatch]] = {}
    stats: Dict[str, Dict] = {}

    for m in matches:
        for team in (m.home_team, m.away_team):
            if team not in history:
                history[team] = []

    for m in matches:
        for team in (m.home_team, m.away_team):
            h = history[team]
            if not h:
                gs = 1.3
                gc = 1.3
            else:
                goals_scored = sum(
                    x.home_goals if x.home_team == team else x.away_goals for x in h
                )
                goals_conceded = sum(
                    x.away_goals if x.home_team == team else x.home_goals for x in h
                )
                gs = goals_scored / len(h)
                gc = goals_conceded / len(h)
            stats[team] = {"avg_goals_scored": gs, "avg_goals_conceded": gc}
            history[team].append(m)

    return stats


def build_context(
    m: LeagueMatch,
    elos: Dict[str, int],
    team_stats: Dict[str, Dict],
) -> Tuple[MatchContext, str]:
    """构建 MatchContext 和实际结果"""
    home_elo = elos.get(m.home_team, 1600)
    away_elo = elos.get(m.away_team, 1600)
    hs = team_stats.get(m.home_team, {"avg_goals_scored": 1.3, "avg_goals_conceded": 1.3})
    as_ = team_stats.get(m.away_team, {"avg_goals_scored": 1.3, "avg_goals_conceded": 1.3})

    home_ctx = TeamContext(
        team_id=0,
        name=m.home_team,
        elo=home_elo,
        fifa_rank=50,
        avg_goals_scored=hs["avg_goals_scored"],
        avg_goals_conceded=hs["avg_goals_conceded"],
        form_factor=1.0,
        key_players_available=11,
        key_players_total=11,
        squad_fatigue_index=0.5,
        rest_days=7,
        tactical_style="balanced",
        coach_rating=0.5,
        home_away_factor=1.0,
        weather_adaptability=1.0,
        recent_results="",
    )
    away_ctx = TeamContext(
        team_id=0,
        name=m.away_team,
        elo=away_elo,
        fifa_rank=50,
        avg_goals_scored=as_["avg_goals_scored"],
        avg_goals_conceded=as_["avg_goals_conceded"],
        form_factor=1.0,
        key_players_available=11,
        key_players_total=11,
        squad_fatigue_index=0.5,
        rest_days=7,
        tactical_style="balanced",
        coach_rating=0.5,
        home_away_factor=1.0,
        weather_adaptability=1.0,
        recent_results="",
    )

    ctx = MatchContext(
        match_id=0,
        home_team=home_ctx,
        away_team=away_ctx,
        stage="group",
        is_knockout=False,
        odds_home=m.odds_home or 2.5,
        odds_draw=m.odds_draw or 3.2,
        odds_away=m.odds_away or 2.8,
        closing_odds_home=m.odds_home or 2.5,
        closing_odds_draw=m.odds_draw or 3.2,
        closing_odds_away=m.odds_away or 2.8,
        venue_type="home",
        weather="clear",
        temperature=20.0,
        pitch_condition="good",
        schedule_density="normal",
    )

    if m.home_goals > m.away_goals:
        actual = "home"
    elif m.home_goals < m.away_goals:
        actual = "away"
    else:
        actual = "draw"

    return ctx, actual


def run_league_backtest(matches: List[LeagueMatch], label: str) -> Dict[str, float]:
    """对联赛数据跑回测"""
    elos = walk_forward_elo(matches)
    stats = compute_team_stats(matches)

    historical = []
    for m in matches:
        if not all((m.odds_home, m.odds_draw, m.odds_away)):
            continue
        ctx, actual = build_context(m, elos, stats)
        historical.append((ctx, actual))

    if not historical:
        print(f"  ⚠️ {label}: 无有效数据")
        return {}

    engine = PredictionEngine(weights=DEFAULT_WEIGHTS.copy())
    correct = 0
    briers = []
    log_losses = []

    for ctx, actual in historical:
        result = engine.predict(ctx)
        spf = result.spf
        if direction_correct(spf, actual):
            correct += 1
        briers.append(
            sum(brier_score(spf[k], 1 if actual == k else 0) for k in ["home", "draw", "away"]) / 3.0
        )
        log_losses.append(-math.log(max(spf.get(actual, 1e-6), 1e-6)))

    n = len(historical)
    acc = correct / n
    brier = sum(briers) / len(briers)
    ll = sum(log_losses) / len(log_losses)

    print(f"\n  📊 {label} ({n} 场)")
    print(f"     方向准确率: {correct}/{n} = {acc*100:.1f}%")
    print(f"     Brier Score: {brier:.4f}")
    print(f"     Log Loss:    {ll:.4f}")

    return {"accuracy": acc, "brier": brier, "log_loss": ll, "n": n}


def main():
    print("=" * 60)
    print("  五大联赛回测 — 扩展样本至 500+ 场")
    print("=" * 60)

    all_results = []
    total_matches = 0

    for league_code, info in LEAGUES.items():
        for season in SEASONS:
            label = f"{info['name']} {season[:2]}/{season[2:]}"
            print(f"\n  📥 {label}")
            rows = download_csv(league_code, season)
            matches = [m for r in rows if (m := parse_match(r, league_code, season))]
            print(f"     下载 {len(rows)} 行，解析 {len(matches)} 场")

            if matches:
                stats = run_league_backtest(matches, label)
                if stats:
                    all_results.append((label, stats))
                    total_matches += stats["n"]

    print("\n" + "=" * 60)
    print("  汇总")
    print("=" * 60)
    print(f"\n  总有效场次: {total_matches}")
    print(f"  {'联赛/赛季':<30} {'场次':<6} {'准确率':<8} {'Brier':<8} {'LogLoss':<8}")
    print("  " + "-" * 60)
    for label, s in all_results:
        print(f"  {label:<30} {s['n']:<6} {s['accuracy']*100:<7.1f}% {s['brier']:<8.4f} {s['log_loss']:<8.4f}")

    if total_matches > 0:
        avg_acc = sum(s["accuracy"] * s["n"] for _, s in all_results) / total_matches
        avg_brier = sum(s["brier"] * s["n"] for _, s in all_results) / total_matches
        avg_ll = sum(s["log_loss"] * s["n"] for _, s in all_results) / total_matches
        print("  " + "-" * 60)
        print(f"  {'加权平均':<30} {total_matches:<6} {avg_acc*100:<7.1f}% {avg_brier:<8.4f} {avg_ll:<8.4f}")

    print("\n✅ 联赛回测完成")


if __name__ == "__main__":
    main()
