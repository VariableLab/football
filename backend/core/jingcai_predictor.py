"""
中国足彩（竞彩足球）预测模块

功能：
  1. 从 CSV 导入当前足彩在售比赛
  2. 自动查找/创建球队（内置俱乐部 ELO 映射）
  3. 用预测引擎生成全部 5 种玩法的概率
  4. 输出足彩格式的预测报告（含投注策略）
  5. 预测结果写入数据库，可供前端展示

用法：
    cd backend
    python jingcai_predictor.py import data/jingcai_matches.csv
    python jingcai_predictor.py predict --league EPL --date 2026-05-10
    python jingcai_predictor.py audit

CSV 格式：
    home_team,away_team,handicap,odds_home,odds_draw,odds_away,league,kickoff_at
    曼城,阿森纳,0,2.10,3.40,3.20,英超,2026-05-10 23:30
"""

from __future__ import annotations

import sys
import csv
import json
import argparse
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import List, Dict, Tuple, Optional, Any

from sqlalchemy.orm import Session

from database.models import (
    SessionLocal, Team, Match, Prediction, MatchStatus, MatchType,
    PlayType, JingcaiIssue, JingcaiIssueMatch,
)
from core.prediction_engine import (
    PredictionEngine, MatchContext, build_context_from_match,
    StrategyPick,
)
from strategy_pipeline import StrategyPipeline
from utils.logger import get_logger

logger = get_logger("jingcai")


# ─────────────────────────────────────────
# 内置俱乐部 ELO 映射（2026年5月近似值）
# ─────────────────────────────────────────

CLUB_ELO_RATINGS: Dict[str, Tuple[str, int, str]] = {
    # 英超
    "MCI": ("Manchester City", 2040, "EPL"),
    "ARS": ("Arsenal", 1980, "EPL"),
    "LIV": ("Liverpool", 1960, "EPL"),
    "CHE": ("Chelsea", 1880, "EPL"),
    "MUN": ("Manchester United", 1840, "EPL"),
    "TOT": ("Tottenham", 1830, "EPL"),
    "NEW": ("Newcastle", 1820, "EPL"),
    "AVL": ("Aston Villa", 1810, "EPL"),
    "BHA": ("Brighton", 1780, "EPL"),
    "WHU": ("West Ham", 1760, "EPL"),
    "CRY": ("Crystal Palace", 1740, "EPL"),
    "BRE": ("Brentford", 1730, "EPL"),
    "FUL": ("Fulham", 1720, "EPL"),
    "EVE": ("Everton", 1710, "EPL"),
    "NFO": ("Nottingham Forest", 1700, "EPL"),
    "BOU": ("Bournemouth", 1690, "EPL"),
    "WOL": ("Wolves", 1680, "EPL"),
    "LEI": ("Leicester", 1670, "EPL"),
    "IPS": ("Ipswich", 1620, "EPL"),
    "SOU": ("Southampton", 1610, "EPL"),
    # 西甲
    "RMA": ("Real Madrid", 2020, "LaLiga"),
    "BAR": ("Barcelona", 2000, "LaLiga"),
    "ATM": ("Atletico Madrid", 1920, "LaLiga"),
    "ATH": ("Athletic Bilbao", 1830, "LaLiga"),
    "VIL": ("Villarreal", 1810, "LaLiga"),
    "BET": ("Real Betis", 1790, "LaLiga"),
    "SEV": ("Sevilla", 1780, "LaLiga"),
    "SOC": ("Real Sociedad", 1770, "LaLiga"),
    "CEL": ("Celta Vigo", 1740, "LaLiga"),
    "GET": ("Getafe", 1720, "LaLiga"),
    "RAY": ("Rayo Vallecano", 1710, "LaLiga"),
    "VAL": ("Valencia", 1700, "LaLiga"),
    "OSA": ("Osasuna", 1690, "LaLiga"),
    "MLL": ("Mallorca", 1680, "LaLiga"),
    "ESP": ("Espanyol", 1660, "LaLiga"),
    "ALA": ("Alaves", 1650, "LaLiga"),
    "LEG": ("Leganes", 1640, "LaLiga"),
    "LPA": ("Las Palmas", 1630, "LaLiga"),
    "GIR": ("Girona", 1620, "LaLiga"),
    "VALL": ("Valladolid", 1600, "LaLiga"),
    # 德甲
    "BAY": ("Bayern Munich", 2010, "Bundesliga"),
    "LEV": ("Bayer Leverkusen", 1940, "Bundesliga"),
    "DOR": ("Borussia Dortmund", 1890, "Bundesliga"),
    "RBL": ("RB Leipzig", 1880, "Bundesliga"),
    "S04": ("Schalke 04", 1760, "Bundesliga"),
    "WOB": ("Wolfsburg", 1750, "Bundesliga"),
    "HOF": ("Hoffenheim", 1740, "Bundesliga"),
    "SCF": ("Freiburg", 1730, "Bundesliga"),
    "EIN": ("Eintracht Frankfurt", 1720, "Bundesliga"),
    "BSC": ("Hertha Berlin", 1710, "Bundesliga"),
    "M05": ("Mainz 05", 1700, "Bundesliga"),
    "FCA": ("Augsburg", 1690, "Bundesliga"),
    "KOE": ("Cologne", 1680, "Bundesliga"),
    "BWS": ("Werder Bremen", 1670, "Bundesliga"),
    "UNB": ("Union Berlin", 1660, "Bundesliga"),
    "STU": ("Stuttgart", 1650, "Bundesliga"),
    "BOC": ("Bochum", 1640, "Bundesliga"),
    # 意甲
    "INT": ("Inter Milan", 1990, "SerieA"),
    "JUV": ("Juventus", 1940, "SerieA"),
    "MIL": ("AC Milan", 1920, "SerieA"),
    "NAP": ("Napoli", 1910, "SerieA"),
    "ROM": ("Roma", 1860, "SerieA"),
    "ATA": ("Atalanta", 1850, "SerieA"),
    "LAZ": ("Lazio", 1820, "SerieA"),
    "FIO": ("Fiorentina", 1800, "SerieA"),
    "BOL": ("Bologna", 1780, "SerieA"),
    "TOR": ("Torino", 1750, "SerieA"),
    "UDI": ("Udinese", 1740, "SerieA"),
    "MON": ("Monza", 1720, "SerieA"),
    "GEN": ("Genoa", 1710, "SerieA"),
    "SAS": ("Sassuolo", 1700, "SerieA"),
    "LEC": ("Lecce", 1690, "SerieA"),
    "EMP": ("Empoli", 1680, "SerieA"),
    "VER": ("Verona", 1670, "SerieA"),
    "CAG": ("Cagliari", 1660, "SerieA"),
    "FRO": ("Frosinone", 1640, "SerieA"),
    "SAL": ("Salernitana", 1620, "SerieA"),
    # 法甲
    "PSG": ("Paris Saint-Germain", 1980, "Ligue1"),
    "ASM": ("Monaco", 1860, "Ligue1"),
    "MAR": ("Marseille", 1840, "Ligue1"),
    "LIL": ("Lille", 1820, "Ligue1"),
    "REN": ("Rennes", 1800, "Ligue1"),
    "NIC": ("Nice", 1780, "Ligue1"),
    "OL":  ("Lyon", 1770, "Ligue1"),
    "LEN": ("Lens", 1760, "Ligue1"),
    "STR": ("Strasbourg", 1740, "Ligue1"),
    "REI": ("Reims", 1730, "Ligue1"),
    "MHS": ("Montpellier", 1720, "Ligue1"),
    "NAN": ("Nantes", 1710, "Ligue1"),
    "TFC": ("Toulouse", 1700, "Ligue1"),
    "BRS": ("Brest", 1690, "Ligue1"),
    "MET": ("Metz", 1680, "Ligue1"),
    "CLE": ("Clermont", 1670, "Ligue1"),
    "LEH": ("Le Havre", 1660, "Ligue1"),
    "LOR": ("Lorient", 1650, "Ligue1"),
}

# 中文名 → Code 映射
CHINESE_ALIASES: Dict[str, str] = {
    "曼城": "MCI", "曼联": "MUN", "利物浦": "LIV", "阿森纳": "ARS",
    "切尔西": "CHE", "热刺": "TOT", "纽卡斯尔": "NEW", "纽卡": "NEW",
    "维拉": "AVL", "布莱顿": "BHA", "西汉姆": "WHU", "水晶宫": "CRY",
    "布伦特福德": "BRE", "富勒姆": "FUL", "埃弗顿": "EVE", "诺丁汉森林": "NFO",
    "伯恩茅斯": "BOU", "狼队": "WOL", "莱斯特城": "LEI", "莱斯特": "LEI",
    "伊普斯维奇": "IPS", "南安普顿": "SOU", "南安普敦": "SOU",
    "皇马": "RMA", "皇家马德里": "RMA", "巴萨": "BAR", "巴塞罗那": "BAR",
    "马竞": "ATM", "马德里竞技": "ATM", "毕尔巴鄂": "ATH", "比利亚雷亚尔": "VIL",
    "贝蒂斯": "BET", "塞维利亚": "SEV", "皇家社会": "SOC", "塞尔塔": "CEL",
    "赫塔菲": "GET", "巴列卡诺": "RAY", "瓦伦西亚": "VAL", "奥萨苏纳": "OSA",
    "马洛卡": "MLL", "西班牙人": "ESP", "阿拉维斯": "ALA", "莱加内斯": "LEG",
    "拉斯帕尔马斯": "LPA", "赫罗纳": "GIR", "巴拉多利德": "VALL",
    "拜仁": "BAY", "拜仁慕尼黑": "BAY", "勒沃库森": "LEV", "多特蒙德": "DOR",
    "莱比锡": "RBL", "RB莱比锡": "RBL", "沙尔克": "S04", "沙尔克04": "S04",
    "沃尔夫斯堡": "WOB", "霍芬海姆": "HOF", "弗赖堡": "SCF", "法兰克福": "EIN",
    "柏林赫塔": "BSC", "美因茨": "M05", "奥格斯堡": "FCA", "科隆": "KOE",
    "云达不莱梅": "BWS", "不莱梅": "BWS", "柏林联合": "UNB", "斯图加特": "STU",
    "波鸿": "BOC",
    "国米": "INT", "国际米兰": "INT", "尤文": "JUV", "尤文图斯": "JUV",
    "米兰": "MIL", "AC米兰": "MIL", "那不勒斯": "NAP", "罗马": "ROM",
    "亚特兰大": "ATA", "拉齐奥": "LAZ", "佛罗伦萨": "FIO", "博洛尼亚": "BOL",
    "都灵": "TOR", "乌迪内斯": "UDI", "蒙扎": "MON", "热那亚": "GEN",
    "萨索洛": "SAS", "莱切": "LEC", "恩波利": "EMP", "维罗纳": "VER",
    "卡利亚里": "CAG", "弗罗西诺内": "FRO", "萨勒尼塔纳": "SAL",
    "巴黎": "PSG", "巴黎圣日耳曼": "PSG", "摩纳哥": "ASM", "马赛": "MAR",
    "里尔": "LIL", "雷恩": "REN", "尼斯": "NIC", "里昂": "OL",
    "朗斯": "LEN", "斯特拉斯堡": "STR", "兰斯": "REI", "蒙彼利埃": "MHS",
    "南特": "NAN", "图卢兹": "TFC", "布雷斯特": "BRS", "梅斯": "MET",
    "克莱蒙": "CLE", "勒阿弗尔": "LEH", "洛里昂": "LOR",
}


def resolve_team_code(name: str) -> Optional[str]:
    """将中文名/英文名/code 解析为标准 code"""
    name = name.strip()
    # 直接是 code
    if name.upper() in CLUB_ELO_RATINGS:
        return name.upper()
    # 中文别名
    if name in CHINESE_ALIASES:
        return CHINESE_ALIASES[name]
    # 英文名模糊匹配
    name_lower = name.lower()
    for code, (en_name, elo, league) in CLUB_ELO_RATINGS.items():
        if name_lower == en_name.lower() or name_lower in en_name.lower():
            return code
    return None


# ─────────────────────────────────────────
# 球队管理
# ─────────────────────────────────────────

def get_or_create_team(db: Session, team_name: str) -> Team:
    """
    查找或创建球队。
    优先用内置 ELO 映射，找不到则创建默认球队。
    """
    code = resolve_team_code(team_name)

    if code:
        # 尝试按 code 查找
        team = db.query(Team).filter(Team.code == code).first()
        if team:
            return team

        # 创建新球队
        en_name, elo, league = CLUB_ELO_RATINGS[code]
        team = Team(
            code=code,
            name=en_name,
            name_en=en_name,
            elo=elo,
            fifa_rank=max(1, int((2100 - elo) / 10)),
            avg_goals_scored=1.4,
            avg_goals_conceded=1.2,
            tactical_style="balanced",
            continent="Europe",
        )
        db.add(team)
        db.commit()
        db.refresh(team)
        logger.info(f"[jingcai] Created team: {en_name} ({code}, elo={elo})")
        return team

    # 完全未知球队：用名字做 code，ELO=1500 基准
    team = db.query(Team).filter(Team.name == team_name).first()
    if team:
        return team

    team = Team(
        code=team_name[:10].upper().replace(" ", ""),
        name=team_name,
        elo=1500,
        fifa_rank=50,
        avg_goals_scored=1.3,
        avg_goals_conceded=1.3,
        tactical_style="balanced",
    )
    db.add(team)
    db.commit()
    db.refresh(team)
    logger.warning(f"[jingcai] Unknown team '{team_name}', created with default ELO=1500")
    return team


# ─────────────────────────────────────────
# 比赛导入
# ─────────────────────────────────────────

@dataclass
class JingcaiMatchInput:
    """足彩比赛输入"""
    home_team: str
    away_team: str
    handicap: int = 0
    odds_home: float = 0.0
    odds_draw: float = 0.0
    odds_away: float = 0.0
    league: str = ""
    kickoff_at: Optional[datetime] = None


def parse_csv(path: str) -> List[JingcaiMatchInput]:
    """解析足彩 CSV"""
    inputs = []
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            kickoff = None
            kt = row.get("kickoff_at", "").strip()
            if kt:
                try:
                    kickoff = datetime.strptime(kt, "%Y-%m-%d %H:%M")
                except ValueError:
                    try:
                        kickoff = datetime.strptime(kt, "%Y-%m-%d")
                    except ValueError:
                        pass

            inputs.append(JingcaiMatchInput(
                home_team=row["home_team"].strip(),
                away_team=row["away_team"].strip(),
                handicap=int(row.get("handicap", 0) or 0),
                odds_home=float(row.get("odds_home", 0) or 0),
                odds_draw=float(row.get("odds_draw", 0) or 0),
                odds_away=float(row.get("odds_away", 0) or 0),
                league=row.get("league", "").strip(),
                kickoff_at=kickoff,
            ))
    return inputs


def import_matches(db: Session, inputs: List[JingcaiMatchInput]) -> List[Match]:
    """导入比赛到数据库"""
    matches = []
    for inp in inputs:
        home = get_or_create_team(db, inp.home_team)
        away = get_or_create_team(db, inp.away_team)

        # 生成 match_code
        date_str = inp.kickoff_at.strftime("%Y%m%d") if inp.kickoff_at else "TBD"
        match_code = f"JC-{date_str}-{home.code}-{away.code}"

        # 检查是否已存在
        existing = db.query(Match).filter(Match.match_code == match_code).first()
        if existing:
            match = existing
            match.status = MatchStatus.SCHEDULED
        else:
            match = Match(
                match_code=match_code,
                home_team_id=home.id,
                away_team_id=away.id,
                kickoff_at=inp.kickoff_at,
                stage="group",  # 联赛视为小组赛
                competition=inp.league or "Jingcai",
                match_type=MatchType.FRIENDLY,  # 联赛用 friendly 类型
                status=MatchStatus.SCHEDULED,
                odds_home=inp.odds_home if inp.odds_home > 0 else None,
                odds_draw=inp.odds_draw if inp.odds_draw > 0 else None,
                odds_away=inp.odds_away if inp.odds_away > 0 else None,
                odds_source="manual" if inp.odds_home > 0 else None,
                venue_type="neutral",
            )
            db.add(match)

        matches.append(match)

    db.commit()
    for m in matches:
        db.refresh(m)

    logger.info(f"[jingcai] Imported {len(matches)} matches")
    return matches


# ─────────────────────────────────────────
# 预测生成
# ─────────────────────────────────────────

def build_jingcai_context(db: Session, match: Match) -> MatchContext:
    """为足彩比赛构建 MatchContext（联赛主场优势修正）"""

    # 从 JingcaiIssueMatch 查找让球数
    jim = db.query(JingcaiIssueMatch).filter(
        JingcaiIssueMatch.match_id == match.id
    ).first()
    handicap = jim.handicap if jim else 0

    ctx = build_context_from_match(match, handicap=handicap)

    # 联赛主场优势（比世界杯中立场更大）
    ctx.venue_type = "home"
    ctx.home_team.home_away_factor = 1.15
    ctx.away_team.home_away_factor = 0.90


    # 如果有手动赔率但没有 closing_odds，把手动赔率同时作为 closing_odds
    # 这样 MarketModel 可以正常工作（虽然是手动输入的，但代表真实市场）
    if match.odds_home and not match.closing_odds_home:
        ctx.closing_odds_home = match.odds_home
        ctx.closing_odds_draw = match.odds_draw
        ctx.closing_odds_away = match.odds_away

    return ctx


def predict_match(db: Session, match: Match, engine: PredictionEngine) -> Dict[str, Any]:
    """为单场比赛生成预测并写入数据库"""
    # 清除旧预测
    db.query(Prediction).filter(Prediction.match_id == match.id).delete()

    ctx = build_jingcai_context(db, match)
    result = engine.predict(ctx)

    # 写入数据库
    for payload in result.to_db_payload():
        pred = Prediction(
            match_id=match.id,
            play_type=payload["play_type"],
            probabilities=payload["probabilities"],
            model_version=result.model_version,
        )
        db.add(pred)

    match.confidence = result.confidence
    db.commit()

    # 构建返回数据
    return {
        "match_code": match.match_code,
        "home_team": match.home_team.name,
        "away_team": match.away_team.name,
        "competition": match.competition,
        "kickoff_at": match.kickoff_at.isoformat() if match.kickoff_at else None,
        "confidence": result.confidence,
        "weights_used": result.weights_used,
        "spf": result.spf,
        "rq": result.rq,
        "score_top5": sorted(result.score.items(), key=lambda x: -x[1])[:5],
        "goals_top5": sorted(result.goals.items(), key=lambda x: float(x[0]) if x[0].isdigit() else 99)[:5],
        "half_top5": sorted(result.half.items(), key=lambda x: -x[1])[:5],
        "raw": {
            "elo": result.raw_elo,
            "poisson": result.raw_poisson,
            "players": result.raw_players,
            "market": result.raw_market,
        },
    }


def generate_strategies(
    match: Match,
    prediction: Dict[str, Any],
    risk_tier: str = "balanced",
) -> List[StrategyPick]:
    """生成投注策略模型估算（使用校准管线）"""
    preds = [
        {"play_type": "SPF", "probabilities": prediction["spf"]},
        {"play_type": "RQ", "probabilities": prediction["rq"]},
    ]
    if "score" in prediction:
        preds.append({"play_type": "SCORE", "probabilities": prediction["score"]})
    if "goals" in prediction:
        preds.append({"play_type": "GOALS", "probabilities": prediction["goals"]})
    if "half" in prediction:
        preds.append({"play_type": "HALF", "probabilities": prediction["half"]})

    pipeline = StrategyPipeline(risk_tier=risk_tier, bankroll=100.0)
    picks = pipeline.generate(
        predictions=preds,
        odds_home=match.odds_home or 2.0,
        odds_draw=match.odds_draw or 3.2,
        odds_away=match.odds_away or 3.5,
        competition=match.competition or "",
        match_id=match.id,
    )

    # 转换为旧 StrategyPick 格式以兼容 report 输出
    return [
        StrategyPick(
            strategy_name=p.strategy_name,
            strategy_type=p.risk_tier,
            play_type=p.play_type,
            play_label=p.play_label,
            selection=p.selection,
            selection_label=p.selection_label,
            probability=p.model_prob_calibrated,
            odds=p.odds,
            ev=p.ev,
            kelly_fraction=p.kelly_raw,
            stake_pct=p.stake_pct,
            confidence=p.confidence,
            rationale=p.rationale,
            risk_level=p.risk_label,
        )
        for p in picks
    ]


# ─────────────────────────────────────────
# 报告输出
# ─────────────────────────────────────────

def print_report(predictions: List[Dict[str, Any]], strategies_map: Dict[str, List[StrategyPick]]):
    """打印足彩格式预测报告"""
    print("\n" + "=" * 80)
    print("  中国足彩（竞彩足球）预测报告")
    print("=" * 80)
    print(f"  生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  比赛数量: {len(predictions)} 场")
    print("=" * 80)

    for pred in predictions:
        match_code = pred["match_code"]
        home = pred["home_team"]
        away = pred["away_team"]
        spf = pred["spf"]
        conf = pred["confidence"]

        print(f"\n{'─' * 80}")
        print(f"  {home}  vs  {away}")
        print(f"  联赛: {pred['competition']} | 信心: {conf.upper()}")
        if pred["kickoff_at"]:
            print(f"  开球: {pred['kickoff_at'][:16].replace('T', ' ')}")

        print("\n  【胜平负】")
        print(f"    主胜: {spf['home']:.1%} | 平局: {spf['draw']:.1%} | 客胜: {spf['away']:.1%}")
        best_spf = max(spf, key=spf.get)
        print(f"    → 模型估算: {'主胜' if best_spf=='home' else '平局' if best_spf=='draw' else '客胜'} ({spf[best_spf]:.1%})")

        print("\n  【比分 TOP 5】")
        for score, prob in pred["score_top5"]:
            print(f"    {score}: {prob:.1%}")

        print("\n  【总进球 TOP 5】")
        for goals, prob in pred["goals_top5"]:
            print(f"    {goals}球: {prob:.1%}")

        # 投注策略
        strategies = strategies_map.get(match_code, [])
        if strategies:
            print("\n  【投注策略】")
            for s in strategies:
                ev_sign = "+" if s.ev > 0 else ""
                print(f"    [{s.strategy_name}] {s.play_label} - {s.selection_label}")
                print(f"      概率: {s.probability:.1%} | 赔率: {s.odds:.2f} | EV: {ev_sign}{s.ev:.1%}")
                print(f"      参考仓位: {s.stake_pct:.1f}% | 风险: {s.risk_level}")
                print(f"      理由: {s.rationale}")

        # 模型拆解
        raw = pred["raw"]
        print("\n  【模型拆解】")
        print(f"    Elo:     主胜={raw['elo']['home']:.1%} 平={raw['elo']['draw']:.1%} 客={raw['elo']['away']:.1%}")
        print(f"    Poisson: 主胜={raw['poisson']['home']:.1%} 平={raw['poisson']['draw']:.1%} 客={raw['poisson']['away']:.1%}")
        print(f"    Players: 战力修正={raw['players']:.3f}")
        if raw.get("market"):
            print(f"    Market:  主胜={raw['market'].get('home', 0):.1%} 平={raw['market'].get('draw', 0):.1%} 客={raw['market'].get('away', 0):.1%}")

    print("\n" + "=" * 80)
    print("  免责声明：本预测仅供娱乐参考，不构成投注建议。请理性购彩。")
    print("=" * 80)


# ─────────────────────────────────────────
# 审计
# ─────────────────────────────────────────

def audit_predictions(db: Session) -> Dict[str, Any]:
    """审计足彩预测链路完整性"""
    issues = []
    stats = {
        "total_jingcai_matches": 0,
        "matches_with_predictions": 0,
        "matches_missing_odds": 0,
        "matches_unknown_teams": 0,
        "predictions_by_playtype": {},
        "confidence_distribution": {"high": 0, "medium": 0, "low": 0},
    }

    matches = db.query(Match).filter(Match.match_code.like("JC-%")).all()
    stats["total_jingcai_matches"] = len(matches)

    for match in matches:
        # 检查预测
        preds = db.query(Prediction).filter(Prediction.match_id == match.id).all()
        if preds:
            stats["matches_with_predictions"] += 1
            for p in preds:
                pt = p.play_type.value if hasattr(p.play_type, "value") else str(p.play_type)
                stats["predictions_by_playtype"][pt] = stats["predictions_by_playtype"].get(pt, 0) + 1
        else:
            issues.append(f"{match.match_code}: 无预测")

        # 检查赔率
        if not match.odds_home:
            stats["matches_missing_odds"] += 1
            issues.append(f"{match.match_code}: 缺少赔率")

        # 检查球队
        if match.home_team and match.home_team.elo == 1500:
            stats["matches_unknown_teams"] += 1
        if match.away_team and match.away_team.elo == 1500:
            stats["matches_unknown_teams"] += 1

        # 信心分布
        if match.confidence:
            stats["confidence_distribution"][match.confidence] = stats["confidence_distribution"].get(match.confidence, 0) + 1

    # 检查权重
    from database.models import FusionWeight
    fw_count = db.query(FusionWeight).filter(FusionWeight.is_active == True).count()

    print("\n" + "=" * 60)
    print("  足彩预测链路审计报告")
    print("=" * 60)
    print("\n  统计:")
    print(f"    足彩比赛总数:     {stats['total_jingcai_matches']}")
    print(f"    已生成预测:       {stats['matches_with_predictions']}")
    print(f"    缺少赔率:         {stats['matches_missing_odds']}")
    print(f"    未知球队(ELO=1500): {stats['matches_unknown_teams']}")
    print(f"    活跃权重配置:     {fw_count}")
    print("\n  信心分布:")
    for k, v in stats["confidence_distribution"].items():
        print(f"    {k}: {v}")
    print("\n  玩法覆盖:")
    for k, v in stats["predictions_by_playtype"].items():
        print(f"    {k}: {v}")

    if issues:
        print(f"\n  ⚠️  发现 {len(issues)} 个问题:")
        for i in issues[:10]:
            print(f"    - {i}")
        if len(issues) > 10:
            print(f"    ... 还有 {len(issues) - 10} 个")
    else:
        print("\n  ✅ 审计通过，无异常")

    print("=" * 60)
    return {"stats": stats, "issues": issues}


# ─────────────────────────────────────────
# 足彩期号管理
# ─────────────────────────────────────────

def create_issue(
    db: Session,
    issue_id: str,
    issue_type: str = "spf14",
    sale_start: Optional[datetime] = None,
    sale_end: Optional[datetime] = None,
    match_codes: List[str] = None,
) -> JingcaiIssue:
    """创建足彩期号，并关联比赛"""
    existing = db.query(JingcaiIssue).filter(JingcaiIssue.issue_id == issue_id).first()
    if existing:
        raise ValueError(f"期号 {issue_id} 已存在")

    issue = JingcaiIssue(
        issue_id=issue_id,
        issue_type=issue_type,
        status="on_sale",
        sale_start=sale_start,
        sale_end=sale_end,
    )
    db.add(issue)
    db.commit()
    db.refresh(issue)

    if match_codes:
        for seq, code in enumerate(match_codes, 1):
            match = db.query(Match).filter(Match.match_code == code).first()
            if not match:
                logger.warning(f"[jingcai] Match {code} not found, skipping")
                continue
            link = JingcaiIssueMatch(
                issue_id=issue.id,
                match_id=match.id,
                sequence=seq,
            )
            db.add(link)
        db.commit()
        db.refresh(issue)

    logger.info(f"[jingcai] Created issue {issue_id} ({issue_type}) with {len(match_codes or [])} matches")
    return issue


def add_match_to_issue(db: Session, issue_id: str, match_code: str, sequence: int = 0, handicap: int = 0):
    """向期号添加单场比赛"""
    issue = db.query(JingcaiIssue).filter(JingcaiIssue.issue_id == issue_id).first()
    if not issue:
        raise ValueError(f"期号 {issue_id} 不存在")

    match = db.query(Match).filter(Match.match_code == match_code).first()
    if not match:
        raise ValueError(f"比赛 {match_code} 不存在")

    if sequence == 0:
        # 自动分配序号
        max_seq = db.query(JingcaiIssueMatch).filter(
            JingcaiIssueMatch.issue_id == issue.id
        ).count()
        sequence = max_seq + 1

    link = JingcaiIssueMatch(
        issue_id=issue.id,
        match_id=match.id,
        sequence=sequence,
        handicap=handicap,
    )
    db.add(link)
    db.commit()
    logger.info(f"[jingcai] Added match {match_code} to issue {issue_id} as #{sequence}")
    return link


def predict_issue(db: Session, issue_id: str) -> Dict[str, Any]:
    """为整期足彩生成预测"""
    issue = db.query(JingcaiIssue).filter(JingcaiIssue.issue_id == issue_id).first()
    if not issue:
        raise ValueError(f"期号 {issue_id} 不存在")

    engine = PredictionEngine(db_session=db)
    issue_matches = (
        db.query(JingcaiIssueMatch)
        .filter(JingcaiIssueMatch.issue_id == issue.id)
        .order_by(JingcaiIssueMatch.sequence)
        .all()
    )

    predictions = []
    strategies_map = {}
    for im in issue_matches:
        match = im.match
        pred = predict_match(db, match, engine)
        predictions.append(pred)
        strategies_map[pred["match_code"]] = generate_strategies(match, pred)

    # 整期模型估算汇总
    spf_recommendations = []
    for pred in predictions:
        spf = pred["spf"]
        best = max(spf, key=spf.get)
        spf_recommendations.append({
            "match_code": pred["match_code"],
            "home": pred["home_team"],
            "away": pred["away_team"],
            "pick": "3" if best == "home" else "1" if best == "draw" else "0",
            "confidence": pred["confidence"],
            "prob": spf[best],
        })

    print_report(predictions, strategies_map)

    # 打印整期汇总
    print(f"\n{'='*80}")
    print(f"  足彩 {issue_id} 期 14场胜负模型估算")
    print(f"{'='*80}")
    for r in spf_recommendations:
        conf_emoji = {"high": "★", "medium": "◆", "low": "·"}.get(r["confidence"], "·")
        print(f"  {r['match_code'][:20]:20s} {r['pick']}  {conf_emoji} {r['prob']:.1%}")
    print(f"{'='*80}")

    return {
        "issue_id": issue_id,
        "predictions": predictions,
        "spf_recommendations": spf_recommendations,
    }


def record_draw_result(
    db: Session,
    issue_id: str,
    results: List[str],
    prizes: Optional[Dict[str, Any]] = None,
    draw_at: Optional[datetime] = None,
) -> JingcaiIssue:
    """录入开奖结果"""
    issue = db.query(JingcaiIssue).filter(JingcaiIssue.issue_id == issue_id).first()
    if not issue:
        raise ValueError(f"期号 {issue_id} 不存在")

    issue.draw_result = {
        "results": results,
        "prizes": prizes or {},
    }
    issue.draw_at = draw_at or datetime.utcnow()
    issue.status = "drawn"
    db.commit()
    db.refresh(issue)

    logger.info(f"[jingcai] Recorded draw result for issue {issue_id}")
    return issue


def verify_issue(db: Session, issue_id: str) -> Dict[str, Any]:
    """验证模型预测 vs 开奖结果"""
    issue = db.query(JingcaiIssue).filter(JingcaiIssue.issue_id == issue_id).first()
    if not issue:
        raise ValueError(f"期号 {issue_id} 不存在")

    if not issue.draw_result:
        raise ValueError(f"期号 {issue_id} 尚未开奖")

    results = issue.draw_result.get("results", [])
    is_auto_closed = issue.draw_result.get("auto_closed", False)
    issue_matches = (
        db.query(JingcaiIssueMatch)
        .filter(JingcaiIssueMatch.issue_id == issue.id)
        .order_by(JingcaiIssueMatch.sequence)
        .all()
    )

    # 自动关期：从比赛的实际赛果读取，而非 draw_result
    if is_auto_closed and not results:
        outcome_map = {"home": "3", "draw": "1", "away": "0"}
        results = []
        for im in issue_matches:
            match = im.match
            if match.actual_outcome and match.actual_outcome != "abandoned":
                results.append(outcome_map.get(match.actual_outcome, "0"))
            else:
                results.append(None)
    elif len(results) != len(issue_matches):
        raise ValueError(f"开奖结果数量({len(results)})与比赛数量({len(issue_matches)})不匹配")

    correct = []
    spf_hits = 0
    valid_count = 0
    detail = []

    for im, actual in zip(issue_matches, results):
        match = im.match
        # 无赛果的比赛不参与命中率计算
        if actual is None:
            detail.append({
                "sequence": im.sequence,
                "match_code": match.match_code,
                "home": match.home_team.name if match.home_team else "",
                "away": match.away_team.name if match.away_team else "",
                "predicted": None,
                "actual": None,
                "correct": None,
                "prob": 0,
            })
            continue

        # 获取模型对这场比赛的 SPF 预测
        pred = db.query(Prediction).filter(
            Prediction.match_id == match.id,
            Prediction.play_type == PlayType.SPF,
        ).order_by(Prediction.locked_at.desc()).first()

        if pred and pred.probabilities:
            probs = pred.probabilities
            best = max(probs, key=probs.get)
            predicted = "3" if best == "home" else "1" if best == "draw" else "0"
            is_correct = predicted == actual
            correct.append(is_correct)
            valid_count += 1
            if is_correct:
                spf_hits += 1

            detail.append({
                "sequence": im.sequence,
                "match_code": match.match_code,
                "home": match.home_team.name if match.home_team else "",
                "away": match.away_team.name if match.away_team else "",
                "predicted": predicted,
                "actual": actual,
                "correct": is_correct,
                "prob": probs.get(best, 0),
            })
        else:
            detail.append({
                "sequence": im.sequence,
                "match_code": match.match_code,
                "predicted": None,
                "actual": actual,
                "correct": False,
                "prob": 0,
            })

    # 任选9场命中数（从14场中任选9场组合的最大命中）
    r9_hits = 0
    if len(correct) >= 9:
        from itertools import combinations
        for combo in combinations(range(len(correct)), 9):
            hits = sum(correct[i] for i in combo)
            r9_hits = max(r9_hits, hits)

    verification = {
        "spf_hits": spf_hits,
        "r9_hits": r9_hits,
        "total_matches": len(issue_matches),
        "valid_matches": valid_count,
        "accuracy": spf_hits / valid_count if valid_count > 0 else 0,
        "detail": detail,
    }
    issue.verification = verification
    issue.status = "verified"
    db.commit()

    print(f"\n{'='*60}")
    print(f"  足彩 {issue_id} 期 验证报告")
    print(f"{'='*60}")
    print(f"  14场命中: {spf_hits}/{len(issue_matches)} ({verification['accuracy']:.1%})")
    print(f"  任选9最大命中: {r9_hits}/9")
    for d in detail:
        status = "✅" if d["correct"] else "❌"
        print(f"  #{d['sequence']:2d} {d['predicted'] or '?'} vs {d['actual']} {status} {d['home']} vs {d['away']}")
    print(f"{'='*60}")

    return verification


# ─────────────────────────────────────────
# CLI
# ─────────────────────────────────────────

def cmd_import(csv_path: str):
    """导入 CSV 并生成预测"""
    inputs = parse_csv(csv_path)
    if not inputs:
        print("❌ CSV 为空或解析失败")
        sys.exit(1)

    db = SessionLocal()
    try:
        matches = import_matches(db, inputs)
        engine = PredictionEngine(db_session=db)

        predictions = []
        strategies_map = {}
        for match in matches:
            pred = predict_match(db, match, engine)
            predictions.append(pred)
            strategies_map[pred["match_code"]] = generate_strategies(match, pred)

        print_report(predictions, strategies_map)
        print(f"\n✅ 已导入 {len(matches)} 场比赛并生成预测")
    finally:
        db.close()


def cmd_predict(league: Optional[str] = None, date: Optional[str] = None):
    """为数据库中已有的足彩比赛生成预测"""
    db = SessionLocal()
    try:
        query = db.query(Match).filter(Match.match_code.like("JC-%"))
        if league:
            query = query.filter(Match.competition == league)
        if date:
            dt = datetime.strptime(date, "%Y-%m-%d")
            query = query.filter(
                Match.kickoff_at >= dt,
                Match.kickoff_at < dt + timedelta(days=1)
            )

        matches = query.all()
        if not matches:
            print("❌ 没有找到符合条件的足彩比赛")
            sys.exit(1)

        engine = PredictionEngine(db_session=db)
        predictions = []
        strategies_map = {}
        for match in matches:
            pred = predict_match(db, match, engine)
            predictions.append(pred)
            strategies_map[pred["match_code"]] = generate_strategies(match, pred)

        print_report(predictions, strategies_map)
    finally:
        db.close()


def cmd_audit():
    """运行审计"""
    db = SessionLocal()
    try:
        audit_predictions(db)
    finally:
        db.close()


def cmd_issue_create(issue_id: str, issue_type: str, csv_path: str):
    """创建期号并导入比赛"""
    inputs = parse_csv(csv_path)
    if not inputs:
        print("❌ CSV 为空或解析失败")
        sys.exit(1)

    db = SessionLocal()
    try:
        matches = import_matches(db, inputs)
        match_codes = [m.match_code for m in matches]
        issue = create_issue(
            db, issue_id=issue_id, issue_type=issue_type, match_codes=match_codes
        )
        print(f"✅ 已创建期号 {issue_id} ({issue_type})，关联 {len(match_codes)} 场比赛")
    except ValueError as e:
        print(f"❌ {e}")
        sys.exit(1)
    finally:
        db.close()


def cmd_issue_predict(issue_id: str):
    """为整期生成预测"""
    db = SessionLocal()
    try:
        predict_issue(db, issue_id)
    except ValueError as e:
        print(f"❌ {e}")
        sys.exit(1)
    finally:
        db.close()


def cmd_issue_result(issue_id: str, results_str: str):
    """录入开奖结果"""
    db = SessionLocal()
    try:
        results = [r.strip() for r in results_str.split(",")]
        record_draw_result(db, issue_id, results)
        print(f"✅ 已录入期号 {issue_id} 开奖结果: {results}")
    except ValueError as e:
        print(f"❌ {e}")
        sys.exit(1)
    finally:
        db.close()


def cmd_issue_verify(issue_id: str):
    """验证预测 vs 开奖"""
    db = SessionLocal()
    try:
        verify_issue(db, issue_id)
    except ValueError as e:
        print(f"❌ {e}")
        sys.exit(1)
    finally:
        db.close()


def cmd_issue_list():
    """列出所有期号"""
    db = SessionLocal()
    try:
        issues = db.query(JingcaiIssue).order_by(JingcaiIssue.issue_id.desc()).all()
        print(f"\n{'='*70}")
        print("  足彩期号列表")
        print(f"{'='*70}")
        print(f"  {'期号':<10} {'类型':<8} {'状态':<10} {'比赛数':<6} {'开奖时间':<20}")
        print(f"  {'-'*60}")
        for issue in issues:
            match_count = len(issue.issue_matches)
            draw_str = issue.draw_at.strftime("%Y-%m-%d %H:%M") if issue.draw_at else "-"
            print(f"  {issue.issue_id:<10} {issue.issue_type:<8} {issue.status:<10} {match_count:<6} {draw_str:<20}")
        print(f"{'='*70}")
    finally:
        db.close()


def cmd_issue_sync(days: int = 3):
    """从竞彩官网同步当前在售比赛，自动创建/更新期号并生成预测"""
    from odds_collector import JingcaiSource
    from datetime import datetime, timedelta

    src = JingcaiSource()
    db = SessionLocal()
    try:
        import traceback as _tb
        today = datetime.now().strftime("%Y-%m-%d")
        end_date = (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d")

        print(f"🔄 正在从竞彩官网获取 {today} ~ {end_date} 在售比赛...")
        api_data = src._fetch_all_pools(today, end_date)

        if not api_data:
            print("❌ 未获取到任何比赛数据")
            sys.exit(1)

        print(f"📋 获取到 {len(api_data)} 场在售比赛")

        engine = PredictionEngine(db_session=db)
        created = 0
        updated = 0
        predicted = 0

        for mid, mdata in api_data.items():
            home_cn = mdata.get("homeTeamAbbName", "")
            away_cn = mdata.get("awayTeamAbbName", "")
            home_code = mdata.get("homeTeamCode", "")
            away_code = mdata.get("awayTeamCode", "")
            league = mdata.get("leagueAbbName", "")
            match_date = mdata.get("matchDate", "")
            match_time = mdata.get("matchTime", "")
            match_num_str = mdata.get("matchNumStr", "")
            had = mdata.get("had", {})
            hhad = mdata.get("hhad", {})

            # 解析赔率
            odds_h = _safe_float(had.get("h"))
            odds_d = _safe_float(had.get("d"))
            odds_a = _safe_float(had.get("a"))
            goal_line = hhad.get("goalLine", "0")
            try:
                handicap = int(float(goal_line))
            except (ValueError, TypeError):
                handicap = 0

            if None in (odds_h, odds_d, odds_a):
                continue

            # 解析开球时间
            try:
                kickoff = datetime.strptime(f"{match_date} {match_time}", "%Y-%m-%d %H:%M:%S")
            except ValueError:
                kickoff = None

            # 查找或创建球队
            home_team = _get_or_create_jingcai_team(db, home_cn, home_code, league)
            away_team = _get_or_create_jingcai_team(db, away_cn, away_code, league)

            # 生成比赛编码
            match_code = f"JC-{match_date.replace('-', '')}-{home_code}-{away_code}"

            # 查找或创建比赛
            match = db.query(Match).filter(Match.match_code == match_code).first()
            if not match:
                match = Match(
                    match_code=match_code,
                    home_team_id=home_team.id,
                    away_team_id=away_team.id,
                    kickoff_at=kickoff,
                    competition=league,
                    match_type=MatchType.FRIENDLY,
                    stage="group",
                    status=MatchStatus.SCHEDULED,
                    odds_home=odds_h,
                    odds_draw=odds_d,
                    odds_away=odds_a,
                    closing_odds_home=odds_h,
                    closing_odds_draw=odds_d,
                    closing_odds_away=odds_a,
                    venue_type="home",
                )
                db.add(match)
                db.commit()
                db.refresh(match)
                created += 1
            else:
                # 更新赔率
                match.odds_home = odds_h
                match.odds_draw = odds_d
                match.odds_away = odds_a
                match.closing_odds_home = odds_h
                match.closing_odds_draw = odds_d
                match.closing_odds_away = odds_a
                updated += 1
                db.commit()

            # 创建/更新期号关联（按日期分期）
            issue_id = f"JC{match_date.replace('-', '')}"
            issue = db.query(JingcaiIssue).filter(JingcaiIssue.issue_id == issue_id).first()
            if not issue:
                issue = JingcaiIssue(
                    issue_id=issue_id,
                    issue_type="spf14",
                    status="on_sale",
                )
                db.add(issue)
                db.commit()
                db.refresh(issue)

            # 关联比赛到期号（含让球数）
            existing_link = db.query(JingcaiIssueMatch).filter(
                JingcaiIssueMatch.issue_id == issue.id,
                JingcaiIssueMatch.match_id == match.id,
            ).first()
            if existing_link:
                # 更新已有关联的赔率数据
                existing_link.handicap = handicap
                if mdata.get('hhad'):
                    existing_link.rq_odds = json.dumps(mdata.get('hhad', {}), ensure_ascii=False)
                if mdata.get('crs'):
                    existing_link.score_odds = json.dumps(mdata.get('crs', {}), ensure_ascii=False)
                if mdata.get('ttg'):
                    existing_link.goals_odds = json.dumps(mdata.get('ttg', {}), ensure_ascii=False)
                if mdata.get('hafu'):
                    existing_link.half_odds = json.dumps(mdata.get('hafu', {}), ensure_ascii=False)
                db.commit()
            if not existing_link:
                seq = db.query(JingcaiIssueMatch).filter(
                    JingcaiIssueMatch.issue_id == issue.id
                ).count() + 1
                link = JingcaiIssueMatch(
                    issue_id=issue.id,
                    match_id=match.id,
                    sequence=seq,
                    handicap=handicap,
                    rq_odds=json.dumps(mdata.get('hhad', {}), ensure_ascii=False) if mdata.get('hhad') else None,
                    score_odds=json.dumps(mdata.get('crs', {}), ensure_ascii=False) if mdata.get('crs') else None,
                    goals_odds=json.dumps(mdata.get('ttg', {}), ensure_ascii=False) if mdata.get('ttg') else None,
                    half_odds=json.dumps(mdata.get('hafu', {}), ensure_ascii=False) if mdata.get('hafu') else None,
                )
                db.add(link)
                db.commit()

            # 生成预测
            try:
                predict_match(db, match, engine)
                predicted += 1
            except Exception as e:
                logger.warning(f"[jingcai] Predict failed for {match_code}: {e}")
                _tb.print_exc()


        print("=" * 60)
        print(" 同步完成")
        print("=" * 60)
        print(f"  新建比赛: {created}")
        print(f"  更新赔率: {updated}")
        print(f"  生成预测: {predicted}/{len(api_data)}")
        print("=" * 60)

    finally:
        db.close()
        src.close()


def _get_or_create_jingcai_team(db: Session, cn_name: str, code: str, league: str) -> Team:
    """根据中文名查找或创建球队（优先使用内置映射，处理 code 冲突）"""
    # 1. 尝试通过中文别名找到内置球队
    internal_code = resolve_team_code(cn_name)
    if internal_code:
        team = db.query(Team).filter(Team.code == internal_code).first()
        if team:
            return team
        # 创建内置球队
        if internal_code in CLUB_ELO_RATINGS:
            en_name, elo, lg = CLUB_ELO_RATINGS[internal_code]
            team = Team(
                code=internal_code,
                name=cn_name or en_name,
                name_en=en_name,
                elo=elo,
                fifa_rank=max(1, int((2100 - elo) / 10)),
                avg_goals_scored=1.4,
                avg_goals_conceded=1.2,
                tactical_style="balanced",
                continent="Europe",
            )
            db.add(team)
            db.commit()
            db.refresh(team)
            return team

    # 2. 按中文名查找已有球队
    team = db.query(Team).filter(Team.name == cn_name).first()
    if team:
        return team

    # 3. 按竞彩code查找（可能已存在同名不同联赛球队）
    if code:
        team = db.query(Team).filter(Team.code == code).first()
        if team and team.name == cn_name:
            return team

    # 4. 创建新球队 — 处理 code 冲突
    final_code = code or cn_name[:6].upper()
    # 确保code唯一
    existing = db.query(Team).filter(Team.code == final_code).first()
    if existing:
        final_code = f"{code}_{league[:3].upper()}" if code else cn_name[:8].upper()
        existing2 = db.query(Team).filter(Team.code == final_code).first()
        if existing2:
            import hashlib
            suffix = hashlib.md5(cn_name.encode()).hexdigest()[:4].upper()
            final_code = f"{code or 'TM'}_{suffix}"

    team = Team(
        code=final_code,
        name=cn_name,
        name_en=code or cn_name,
        elo=1500,
        fifa_rank=50,
        avg_goals_scored=1.4,
        avg_goals_conceded=1.2,
        tactical_style="balanced",
        continent="Europe",
    )
    db.add(team)
    db.commit()
    db.refresh(team)
    logger.info(f"[jingcai] Created team: {cn_name} ({final_code}, elo=1500)")
    return team



def _safe_float(val):
    """Convert val to float; return None for invalid values."""
    try:
        f = float(val)
        return f if f > 0 else None
    except (ValueError, TypeError):
        return None


def main():
    parser = argparse.ArgumentParser(description="中国足彩预测模块")
    sub = parser.add_subparsers(dest="cmd")

    p_import = sub.add_parser("import", help="从 CSV 导入比赛并预测")
    p_import.add_argument("csv", help="CSV 文件路径")

    p_predict = sub.add_parser("predict", help="为已有比赛生成预测")
    p_predict.add_argument("--league", help="联赛筛选")
    p_predict.add_argument("--date", help="日期筛选 (YYYY-MM-DD)")

    sub.add_parser("audit", help="审计预测链路")

    # 期号管理
    p_issue = sub.add_parser("issue", help="足彩期号管理")
    issue_sub = p_issue.add_subparsers(dest="issue_cmd")

    p_issue_create = issue_sub.add_parser("create", help="创建期号")
    p_issue_create.add_argument("issue_id", help="期号（如 25060）")
    p_issue_create.add_argument("--type", dest="issue_type", default="spf14", help="玩法类型: spf14/r9/half6/goals4")
    p_issue_create.add_argument("csv", help="比赛 CSV 文件路径")

    p_issue_predict = issue_sub.add_parser("predict", help="为整期生成预测")
    p_issue_predict.add_argument("issue_id", help="期号")

    p_issue_result = issue_sub.add_parser("result", help="录入开奖结果")
    p_issue_result.add_argument("issue_id", help="期号")
    p_issue_result.add_argument("results", help="开奖结果，逗号分隔（如 3,1,0,3,3,1,0,1,3,0,1,3,3,1）")

    p_issue_verify = issue_sub.add_parser("verify", help="验证预测 vs 开奖")
    p_issue_verify.add_argument("issue_id", help="期号")

    issue_sub.add_parser("list", help="列出所有期号")

    p_issue_sync = issue_sub.add_parser("sync", help="从竞彩官网同步在售比赛并生成预测")
    p_issue_sync.add_argument("--days", type=int, default=3, help="同步未来N天比赛")

    args = parser.parse_args()

    if args.cmd == "import":
        cmd_import(args.csv)
    elif args.cmd == "predict":
        cmd_predict(args.league, args.date)
    elif args.cmd == "audit":
        cmd_audit()
    elif args.cmd == "issue":
        if args.issue_cmd == "create":
            cmd_issue_create(args.issue_id, args.issue_type, args.csv)
        elif args.issue_cmd == "predict":
            cmd_issue_predict(args.issue_id)
        elif args.issue_cmd == "result":
            cmd_issue_result(args.issue_id, args.results)
        elif args.issue_cmd == "verify":
            cmd_issue_verify(args.issue_id)
        elif args.issue_cmd == "list":
            cmd_issue_list()
        elif args.issue_cmd == "sync":
            cmd_issue_sync(args.days)
        else:
            p_issue.print_help()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
