"""
数据种子脚本 — 48支世界杯参赛队 + 小组赛 + 热身赛/友谊赛

用法：
    cd backend && python seed.py

会创建：
    - 48 支世界杯参赛球队（含 Elo/排名/场均数据）
    - 12 场世界杯小组赛（不同组焦点战）
    - 8 场热身赛/友谊赛（4场已结束，4场即将开始）
    - 为所有比赛生成预测快照
    - 已结束热身赛会自动更新球队 form_factor 和场均数据
    - 1 个测试用户 + 10 个测试卡密
"""

from datetime import datetime, timedelta
from sqlalchemy.orm import Session

from config import get_settings
from models import init_db, get_db, Team, Match, MatchStatus, MatchType, Prediction, User, LicenseKey, LicenseType
from auth import get_password_hash
from prediction_engine import PredictionEngine, MatchContext, TeamContext
from license_manager import create_license_keys
from logger import get_logger

settings = get_settings()
logger = get_logger("seed")

# ────────────────────────────
# 48支2026世界杯参赛球队
# ────────────────────────────
TEAMS_DATA = [
    # === 亚洲 (8) ===
    {"name": "日本", "name_en": "Japan", "code": "JPN", "flag": "🇯🇵", "elo": 1820, "fifa_rank": 17, "group": "E", "continent": "Asia", "avg_goals_scored": 1.50, "avg_goals_conceded": 0.80, "form_factor": 1.08},
    {"name": "韩国", "name_en": "South Korea", "code": "KOR", "flag": "🇰🇷", "elo": 1780, "fifa_rank": 22, "group": "H", "continent": "Asia", "avg_goals_scored": 1.30, "avg_goals_conceded": 1.10, "form_factor": 1.00},
    {"name": "澳大利亚", "name_en": "Australia", "code": "AUS", "flag": "🇦🇺", "elo": 1750, "fifa_rank": 23, "group": "F", "continent": "Asia", "avg_goals_scored": 1.20, "avg_goals_conceded": 1.20, "form_factor": 0.98},
    {"name": "伊朗", "name_en": "Iran", "code": "IRN", "flag": "🇮🇷", "elo": 1740, "fifa_rank": 20, "group": "D", "continent": "Asia", "avg_goals_scored": 1.10, "avg_goals_conceded": 0.90, "form_factor": 1.02},
    {"name": "沙特阿拉伯", "name_en": "Saudi Arabia", "code": "KSA", "flag": "🇸🇦", "elo": 1650, "fifa_rank": 39, "group": "B", "continent": "Asia", "avg_goals_scored": 0.90, "avg_goals_conceded": 1.50, "form_factor": 0.92},
    {"name": "乌兹别克斯坦", "name_en": "Uzbekistan", "code": "UZB", "flag": "🇺🇿", "elo": 1580, "fifa_rank": 42, "group": "L", "continent": "Asia", "avg_goals_scored": 1.00, "avg_goals_conceded": 1.30, "form_factor": 0.95},
    {"name": "伊拉克", "name_en": "Iraq", "code": "IRQ", "flag": "🇮🇶", "elo": 1550, "fifa_rank": 41, "group": "G", "continent": "Asia", "avg_goals_scored": 0.90, "avg_goals_conceded": 1.40, "form_factor": 0.93},
    {"name": "约旦", "name_en": "Jordan", "code": "JOR", "flag": "🇯🇴", "elo": 1520, "fifa_rank": 43, "group": "A", "continent": "Asia", "avg_goals_scored": 0.80, "avg_goals_conceded": 1.30, "form_factor": 0.94},

    # === 非洲 (9) ===
    {"name": "摩洛哥", "name_en": "Morocco", "code": "MAR", "flag": "🇲🇦", "elo": 1840, "fifa_rank": 12, "group": "D", "continent": "Africa", "avg_goals_scored": 1.40, "avg_goals_conceded": 0.70, "form_factor": 1.10},
    {"name": "塞内加尔", "name_en": "Senegal", "code": "SEN", "flag": "🇸🇳", "elo": 1790, "fifa_rank": 18, "group": "F", "continent": "Africa", "avg_goals_scored": 1.30, "avg_goals_conceded": 0.90, "form_factor": 1.05},
    {"name": "埃及", "name_en": "Egypt", "code": "EGY", "flag": "🇪🇬", "elo": 1720, "fifa_rank": 29, "group": "H", "continent": "Africa", "avg_goals_scored": 1.10, "avg_goals_conceded": 1.00, "form_factor": 1.00},
    {"name": "阿尔及利亚", "name_en": "Algeria", "code": "ALG", "flag": "🇩🇿", "elo": 1700, "fifa_rank": 34, "group": "K", "continent": "Africa", "avg_goals_scored": 1.20, "avg_goals_conceded": 1.10, "form_factor": 0.97},
    {"name": "尼日利亚", "name_en": "Nigeria", "code": "NGA", "flag": "🇳🇬", "elo": 1680, "fifa_rank": 32, "group": "I", "continent": "Africa", "avg_goals_scored": 1.30, "avg_goals_conceded": 1.20, "form_factor": 0.99},
    {"name": "科特迪瓦", "name_en": "Ivory Coast", "code": "CIV", "flag": "🇨🇮", "elo": 1660, "fifa_rank": 33, "group": "G", "continent": "Africa", "avg_goals_scored": 1.10, "avg_goals_conceded": 1.30, "form_factor": 0.96},
    {"name": "喀麦隆", "name_en": "Cameroon", "code": "CMR", "flag": "🇨🇲", "elo": 1640, "fifa_rank": 35, "group": "E", "continent": "Africa", "avg_goals_scored": 1.00, "avg_goals_conceded": 1.30, "form_factor": 0.95},
    {"name": "突尼斯", "name_en": "Tunisia", "code": "TUN", "flag": "🇹🇳", "elo": 1620, "fifa_rank": 36, "group": "L", "continent": "Africa", "avg_goals_scored": 0.90, "avg_goals_conceded": 1.20, "form_factor": 0.94},
    {"name": "南非", "name_en": "South Africa", "code": "RSA", "flag": "🇿🇦", "elo": 1550, "fifa_rank": 45, "group": "I", "continent": "Africa", "avg_goals_scored": 0.80, "avg_goals_conceded": 1.30, "form_factor": 0.92},

    # === 欧洲 (16) ===
    {"name": "法国", "name_en": "France", "code": "FRA", "flag": "🇫🇷", "elo": 2020, "fifa_rank": 2, "group": "B", "continent": "Europe", "avg_goals_scored": 1.90, "avg_goals_conceded": 0.70, "form_factor": 1.12},
    {"name": "英格兰", "name_en": "England", "code": "ENG", "flag": "🏴󠁧󠁢󠁥󠁮󠁧󠁿", "elo": 1980, "fifa_rank": 3, "group": "C", "continent": "Europe", "avg_goals_scored": 1.80, "avg_goals_conceded": 0.60, "form_factor": 1.10},
    {"name": "西班牙", "name_en": "Spain", "code": "ESP", "flag": "🇪🇸", "elo": 1960, "fifa_rank": 8, "group": "G", "continent": "Europe", "avg_goals_scored": 1.70, "avg_goals_conceded": 0.80, "form_factor": 1.08},
    {"name": "德国", "name_en": "Germany", "code": "GER", "flag": "🇩🇪", "elo": 1910, "fifa_rank": 16, "group": "D", "continent": "Europe", "avg_goals_scored": 1.60, "avg_goals_conceded": 1.00, "form_factor": 1.00},
    {"name": "葡萄牙", "name_en": "Portugal", "code": "POR", "flag": "🇵🇹", "elo": 1940, "fifa_rank": 6, "group": "F", "continent": "Europe", "avg_goals_scored": 1.70, "avg_goals_conceded": 0.70, "form_factor": 1.08},
    {"name": "荷兰", "name_en": "Netherlands", "code": "NED", "flag": "🇳🇱", "elo": 1930, "fifa_rank": 7, "group": "E", "continent": "Europe", "avg_goals_scored": 1.60, "avg_goals_conceded": 0.80, "form_factor": 1.06},
    {"name": "意大利", "name_en": "Italy", "code": "ITA", "flag": "🇮🇹", "elo": 1900, "fifa_rank": 9, "group": "H", "continent": "Europe", "avg_goals_scored": 1.50, "avg_goals_conceded": 0.80, "form_factor": 1.02},
    {"name": "比利时", "name_en": "Belgium", "code": "BEL", "flag": "🇧🇪", "elo": 1920, "fifa_rank": 5, "group": "A", "continent": "Europe", "avg_goals_scored": 1.70, "avg_goals_conceded": 1.00, "form_factor": 1.04},
    {"name": "克罗地亚", "name_en": "Croatia", "code": "CRO", "flag": "🇭🇷", "elo": 1880, "fifa_rank": 10, "group": "C", "continent": "Europe", "avg_goals_scored": 1.30, "avg_goals_conceded": 1.00, "form_factor": 1.03},
    {"name": "丹麦", "name_en": "Denmark", "code": "DEN", "flag": "🇩🇰", "elo": 1810, "fifa_rank": 21, "group": "I", "continent": "Europe", "avg_goals_scored": 1.40, "avg_goals_conceded": 1.00, "form_factor": 1.00},
    {"name": "瑞士", "name_en": "Switzerland", "code": "SUI", "flag": "🇨🇭", "elo": 1800, "fifa_rank": 19, "group": "J", "continent": "Europe", "avg_goals_scored": 1.30, "avg_goals_conceded": 1.00, "form_factor": 1.01},
    {"name": "波兰", "name_en": "Poland", "code": "POL", "flag": "🇵🇱", "elo": 1750, "fifa_rank": 26, "group": "J", "continent": "Europe", "avg_goals_scored": 1.20, "avg_goals_conceded": 1.30, "form_factor": 0.96},
    {"name": "塞尔维亚", "name_en": "Serbia", "code": "SRB", "flag": "🇷🇸", "elo": 1740, "fifa_rank": 27, "group": "K", "continent": "Europe", "avg_goals_scored": 1.40, "avg_goals_conceded": 1.30, "form_factor": 0.98},
    {"name": "土耳其", "name_en": "Turkey", "code": "TUR", "flag": "🇹🇷", "elo": 1760, "fifa_rank": 25, "group": "L", "continent": "Europe", "avg_goals_scored": 1.30, "avg_goals_conceded": 1.40, "form_factor": 0.97},
    {"name": "乌克兰", "name_en": "Ukraine", "code": "UKR", "flag": "🇺🇦", "elo": 1730, "fifa_rank": 24, "group": "B", "continent": "Europe", "avg_goals_scored": 1.20, "avg_goals_conceded": 1.10, "form_factor": 0.99},
    {"name": "苏格兰", "name_en": "Scotland", "code": "SCO", "flag": "🏴󠁧󠁢󠁳󠁣󠁴󠁿", "elo": 1710, "fifa_rank": 31, "group": "C", "continent": "Europe", "avg_goals_scored": 1.10, "avg_goals_conceded": 1.30, "form_factor": 0.95},

    # === 南美 (6) ===
    {"name": "阿根廷", "name_en": "Argentina", "code": "ARG", "flag": "🇦🇷", "elo": 2050, "fifa_rank": 1, "group": "A", "continent": "South America", "avg_goals_scored": 1.80, "avg_goals_conceded": 0.50, "form_factor": 1.15},
    {"name": "巴西", "name_en": "Brazil", "code": "BRA", "flag": "🇧🇷", "elo": 1990, "fifa_rank": 4, "group": "B", "continent": "South America", "avg_goals_scored": 1.70, "avg_goals_conceded": 0.70, "form_factor": 1.08},
    {"name": "乌拉圭", "name_en": "Uruguay", "code": "URU", "flag": "🇺🇾", "elo": 1870, "fifa_rank": 11, "group": "C", "continent": "South America", "avg_goals_scored": 1.40, "avg_goals_conceded": 0.80, "form_factor": 1.05},
    {"name": "哥伦比亚", "name_en": "Colombia", "code": "COL", "flag": "🇨🇴", "elo": 1830, "fifa_rank": 14, "group": "D", "continent": "South America", "avg_goals_scored": 1.30, "avg_goals_conceded": 0.90, "form_factor": 1.03},
    {"name": "厄瓜多尔", "name_en": "Ecuador", "code": "ECU", "flag": "🇪🇨", "elo": 1760, "fifa_rank": 28, "group": "E", "continent": "South America", "avg_goals_scored": 1.20, "avg_goals_conceded": 1.20, "form_factor": 0.98},
    {"name": "巴拉圭", "name_en": "Paraguay", "code": "PAR", "flag": "🇵🇾", "elo": 1680, "fifa_rank": 40, "group": "F", "continent": "South America", "avg_goals_scored": 0.90, "avg_goals_conceded": 1.20, "form_factor": 0.93},

    # === 中北美 (6) ===
    {"name": "美国", "name_en": "USA", "code": "USA", "flag": "🇺🇸", "elo": 1820, "fifa_rank": 13, "group": "G", "continent": "North America", "avg_goals_scored": 1.40, "avg_goals_conceded": 1.00, "form_factor": 1.02},
    {"name": "加拿大", "name_en": "Canada", "code": "CAN", "flag": "🇨🇦", "elo": 1700, "fifa_rank": 30, "group": "H", "continent": "North America", "avg_goals_scored": 1.10, "avg_goals_conceded": 1.40, "form_factor": 0.95},
    {"name": "墨西哥", "name_en": "Mexico", "code": "MEX", "flag": "🇲🇽", "elo": 1780, "fifa_rank": 15, "group": "I", "continent": "North America", "avg_goals_scored": 1.30, "avg_goals_conceded": 1.20, "form_factor": 0.98},
    {"name": "哥斯达黎加", "name_en": "Costa Rica", "code": "CRC", "flag": "🇨🇷", "elo": 1600, "fifa_rank": 37, "group": "J", "continent": "North America", "avg_goals_scored": 0.80, "avg_goals_conceded": 1.40, "form_factor": 0.90},
    {"name": "巴拿马", "name_en": "Panama", "code": "PAN", "flag": "🇵🇦", "elo": 1540, "fifa_rank": 46, "group": "K", "continent": "North America", "avg_goals_scored": 0.70, "avg_goals_conceded": 1.50, "form_factor": 0.88},
    {"name": "牙买加", "name_en": "Jamaica", "code": "JAM", "flag": "🇯🇲", "elo": 1500, "fifa_rank": 47, "group": "L", "continent": "North America", "avg_goals_scored": 0.60, "avg_goals_conceded": 1.60, "form_factor": 0.85},

    # === 大洋洲 (1) ===
    {"name": "新西兰", "name_en": "New Zealand", "code": "NZL", "flag": "🇳🇿", "elo": 1520, "fifa_rank": 44, "group": "K", "continent": "Oceania", "avg_goals_scored": 0.70, "avg_goals_conceded": 1.60, "form_factor": 0.87},

    # === 附加赛待定 (2) ===
    {"name": "待定A", "name_en": "TBD A", "code": "TBD", "flag": "🏳️", "elo": 1600, "fifa_rank": 48, "group": "A", "continent": "Asia", "avg_goals_scored": 1.00, "avg_goals_conceded": 1.50, "form_factor": 0.90},
    {"name": "待定B", "name_en": "TBD B", "code": "TBD2", "flag": "🏳️", "elo": 1580, "fifa_rank": 49, "group": "J", "continent": "Africa", "avg_goals_scored": 0.90, "avg_goals_conceded": 1.60, "form_factor": 0.88},
]


def seed_teams(db: Session) -> list[Team]:
    """创建48支世界杯参赛球队"""
    teams = []
    for data in TEAMS_DATA:
        t = Team(**data)
        db.add(t)
        teams.append(t)

    db.commit()
    for t in teams:
        db.refresh(t)

    logger.info(f"Created {len(teams)} teams")
    print(f"✅ Created {len(teams)} teams")
    return teams


def seed_matches(db: Session, teams: list[Team]) -> list[Match]:
    """创建世界杯小组赛 + 热身赛/友谊赛"""
    now = datetime.utcnow()
    team_map = {t.code: t for t in teams}

    # ─── 12场世界杯小组赛（每组1场焦点战）───
    wc_matches = [
        ("WC2026-A1", "ARG", "BEL", "A", 14, (1.65, 3.60, 5.20)),
        ("WC2026-B1", "BRA", "FRA", "B", 15, (2.20, 3.20, 3.30)),
        ("WC2026-C1", "ENG", "URU", "C", 16, (1.55, 3.80, 6.00)),
        ("WC2026-D1", "GER", "COL", "D", 14, (1.75, 3.50, 4.80)),
        ("WC2026-E1", "NED", "JPN", "E", 17, (1.60, 3.70, 5.50)),
        ("WC2026-F1", "POR", "SEN", "F", 15, (1.50, 3.90, 6.50)),
        ("WC2026-G1", "ESP", "USA", "G", 16, (1.45, 4.20, 7.00)),
        ("WC2026-H1", "ITA", "KOR", "H", 14, (1.55, 3.60, 6.20)),
        ("WC2026-I1", "MEX", "DEN", "I", 18, (2.40, 3.10, 3.00)),
        ("WC2026-J1", "SUI", "POL", "J", 15, (1.70, 3.40, 5.20)),
        ("WC2026-K1", "SRB", "NZL", "K", 17, (1.35, 4.50, 9.00)),
        ("WC2026-L1", "TUR", "JAM", "L", 16, (1.30, 4.80, 11.00)),
    ]

    matches = []
    for code, home_code, away_code, group, day_offset, odds in wc_matches:
        home = team_map[home_code]
        away = team_map[away_code]
        m = Match(
            match_code=code,
            home_team_id=home.id,
            away_team_id=away.id,
            kickoff_at=now + timedelta(days=day_offset),
            group=group,
            stage="group",
            match_type=MatchType.WORLD_CUP,
            competition="WC2026",
            status=MatchStatus.SCHEDULED,
            odds_home=odds[0],
            odds_draw=odds[1],
            odds_away=odds[2],
        )
        db.add(m)
        matches.append(m)

    # ─── 8场热身赛/友谊赛（4场已结束，4场即将开始）───
    friendly_matches = [
        # 已结束的热身赛
        ("FRIENDLY-01", "ARG", "ITA", -2, MatchStatus.FINISHED, (1.80, 3.40, 4.50), 2, 1, "home"),
        ("FRIENDLY-02", "BRA", "GER", -3, MatchStatus.FINISHED, (2.10, 3.30, 3.40), 1, 1, "draw"),
        ("FRIENDLY-03", "POR", "BEL", -1, MatchStatus.FINISHED, (2.00, 3.40, 3.70), 3, 2, "home"),
        ("FRIENDLY-04", "USA", "MEX", -4, MatchStatus.FINISHED, (2.30, 3.10, 3.20), 1, 2, "away"),
        # 即将开始的热身赛
        ("FRIENDLY-05", "FRA", "ESP", 1, MatchStatus.SCHEDULED, (1.90, 3.40, 4.00), None, None, None),
        ("FRIENDLY-06", "ENG", "NED", 2, MatchStatus.SCHEDULED, (1.70, 3.50, 5.00), None, None, None),
        ("FRIENDLY-07", "JPN", "KOR", 3, MatchStatus.SCHEDULED, (1.85, 3.30, 4.40), None, None, None),
        ("FRIENDLY-08", "URU", "COL", 5, MatchStatus.SCHEDULED, (2.20, 3.20, 3.30), None, None, None),
    ]

    for code, home_code, away_code, day_offset, status, odds, hg, ag, outcome in friendly_matches:
        home = team_map[home_code]
        away = team_map[away_code]
        m = Match(
            match_code=code,
            home_team_id=home.id,
            away_team_id=away.id,
            kickoff_at=now + timedelta(days=day_offset),
            group=None,
            stage="friendly",
            match_type=MatchType.FRIENDLY,
            competition="International Friendly",
            status=status,
            odds_home=odds[0],
            odds_draw=odds[1],
            odds_away=odds[2],
            actual_home_goals=hg,
            actual_away_goals=ag,
            actual_outcome=outcome,
        )
        db.add(m)
        matches.append(m)

    db.commit()
    for m in matches:
        db.refresh(m)

    logger.info(f"Created {len(matches)} matches ({len(wc_matches)} WC + {len(friendly_matches)} friendly)")
    print(f"✅ Created {len(matches)} matches ({len(wc_matches)} WC + {len(friendly_matches)} friendly)")
    return matches


def update_team_form_from_friendly(db: Session, teams: list[Team], matches: list[Match]):
    """
    根据已结束的热身赛结果，更新球队的 form_factor、avg_goals_scored、avg_goals_conceded。
    使用指数移动平均：新值 = 0.7 * 旧值 + 0.3 * 新比赛数据
    """
    finished_friendly = [m for m in matches
                         if m.match_type == MatchType.FRIENDLY
                         and m.status == MatchStatus.FINISHED
                         and m.actual_home_goals is not None]

    team_map = {t.id: t for t in teams}

    for match in finished_friendly:
        home = team_map[match.home_team_id]
        away = team_map[match.away_team_id]
        hg = match.actual_home_goals
        ag = match.actual_away_goals

        # 更新场均数据（EMA）
        home.avg_goals_scored = 0.7 * home.avg_goals_scored + 0.3 * hg
        home.avg_goals_conceded = 0.7 * home.avg_goals_conceded + 0.3 * ag
        away.avg_goals_scored = 0.7 * away.avg_goals_scored + 0.3 * ag
        away.avg_goals_conceded = 0.7 * away.avg_goals_conceded + 0.3 * hg

        # 更新 form_factor
        if match.actual_outcome == "home":
            home.form_factor = min(1.5, home.form_factor * 1.04)
            away.form_factor = max(0.5, away.form_factor * 0.96)
        elif match.actual_outcome == "away":
            home.form_factor = max(0.5, home.form_factor * 0.96)
            away.form_factor = min(1.5, away.form_factor * 1.04)
        else:  # draw
            # 平局对状态影响较小，强队打平略扣分，弱队打平略加分
            if home.elo > away.elo:
                home.form_factor = max(0.5, home.form_factor * 0.99)
                away.form_factor = min(1.5, away.form_factor * 1.01)
            elif away.elo > home.elo:
                home.form_factor = min(1.5, home.form_factor * 1.01)
                away.form_factor = max(0.5, away.form_factor * 0.99)

        # 更新 form_last5
        home.form_last5 = (home.form_last5 or "")[-4:] + _result_char(hg, ag)
        away.form_last5 = (away.form_last5 or "")[-4:] + _result_char(ag, hg)

        logger.info(
            f"[form-update] {home.name} {hg}-{ag} {away.name} | "
            f"form: {home.form_factor:.3f}/{away.form_factor:.3f}"
        )

    db.commit()
    print(f"✅ Updated team form from {len(finished_friendly)} friendly matches")


def _result_char(goals_for: int, goals_against: int) -> str:
    if goals_for > goals_against:
        return "W"
    elif goals_for < goals_against:
        return "L"
    return "D"


def seed_predictions(db: Session, matches: list[Match], teams: list[Team]):
    """为所有比赛生成预测快照"""
    engine = PredictionEngine()
    team_map = {t.id: t for t in teams}

    for match in matches:
        home = team_map[match.home_team_id]
        away = team_map[match.away_team_id]

        # possession → 战术风格推断校准
        def _infer_tactical(team):
            t = team.tactical_style or "balanced"
            if team.possession and team.possession > 55 and t == "balanced":
                return "attack"
            if team.possession and team.possession < 45 and t == "balanced":
                return "counter"
            return t

        ctx = MatchContext(
            match_id=match.id,
            home_team=TeamContext(
                team_id=home.id,
                name=home.name,
                elo=home.elo,
                fifa_rank=home.fifa_rank,
                avg_goals_scored=home.avg_goals_scored or 1.30,
                avg_goals_conceded=home.avg_goals_conceded or 1.10,
                avg_xg=home.avg_xg or 0.0,
                avg_xga=home.avg_xga or 0.0,
                possession=home.possession or 0.0,
                pass_completion=home.pass_completion or 0.0,
                shots_per_game=home.shots_per_game or 0.0,
                form_factor=home.form_factor or 1.0,
                recent_results=home.recent_results or "",
                recent_goals_scored=home.recent_goals_scored or 0,
                recent_goals_conceded=home.recent_goals_conceded or 0,
                home_away_factor=home.home_away_factor or 1.0,
                weather_adaptability=home.weather_adaptability or 1.0,
                tactical_style=_infer_tactical(home),
                coach_rating=home.coach_rating or 0.5,
                rest_days=home.rest_days or 7,
                key_injuries=home.key_injuries or "",
                squad_fatigue_index=home.squad_fatigue_index or 0.5,
            ),
            away_team=TeamContext(
                team_id=away.id,
                name=away.name,
                elo=away.elo,
                fifa_rank=away.fifa_rank,
                avg_goals_scored=away.avg_goals_scored or 1.20,
                avg_goals_conceded=away.avg_goals_conceded or 1.20,
                avg_xg=away.avg_xg or 0.0,
                avg_xga=away.avg_xga or 0.0,
                possession=away.possession or 0.0,
                pass_completion=away.pass_completion or 0.0,
                shots_per_game=away.shots_per_game or 0.0,
                form_factor=away.form_factor or 1.0,
                recent_results=away.recent_results or "",
                recent_goals_scored=away.recent_goals_scored or 0,
                recent_goals_conceded=away.recent_goals_conceded or 0,
                home_away_factor=away.home_away_factor or 1.0,
                weather_adaptability=away.weather_adaptability or 1.0,
                tactical_style=_infer_tactical(away),
                coach_rating=away.coach_rating or 0.5,
                rest_days=away.rest_days or 7,
                key_injuries=away.key_injuries or "",
                squad_fatigue_index=away.squad_fatigue_index or 0.5,
            ),
            stage=match.stage,
            is_knockout=match.stage != "group" and match.stage != "friendly",
            odds_home=match.odds_home,
            odds_draw=match.odds_draw,
            odds_away=match.odds_away,
            venue_type=match.venue_type or "neutral",
            weather=match.weather or "clear",
            temperature=match.temperature or 20.0,
            pitch_condition=match.pitch_condition or "good",
            schedule_density=match.schedule_density or "normal",
        )

        result = engine.predict(ctx)

        for pred_data in result.to_db_payload():
            pred = Prediction(
                match_id=match.id,
                play_type=pred_data["play_type"],
                probabilities=pred_data["probabilities"],
                model_version="v1.0",
            )
            db.add(pred)

        match.confidence = result.confidence

    db.commit()
    logger.info(f"Created predictions for {len(matches)} matches")
    print(f"✅ Created predictions for {len(matches)} matches")


def seed_user(db: Session) -> User:
    """创建测试用户"""
    user = User(
        email="test@example.com",
        password_hash=get_password_hash("test123"),
        is_active=True,
        is_paid=False,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    logger.info(f"Created test user: {user.email}")
    print(f"✅ Created test user: {user.email} / password: test123")
    return user


def seed_licenses(db: Session):
    """创建测试卡密"""
    keys = create_license_keys(db, LicenseType.TOURNAMENT, count=10)
    logger.info(f"Created {len(keys)} tournament license keys")
    print(f"✅ Created {len(keys)} tournament license keys")
    for k in keys[:3]:
        print(f"   {k.key}")
    print(f"   ... and {len(keys) - 3} more")


def main():
    logger.info("Starting seed script")
    print("=" * 50)
    print("WC Analytics — 数据种子脚本 (48队 + 热身赛)")
    print("=" * 50)

    init_db()
    from models import SessionLocal
    db = SessionLocal()
    try:
        # 检查是否已有数据
        existing = db.query(Team).first()
        if existing:
            logger.info("Database already has data, skipping seed")
            print("\n⚠️  Database already has data. Skipping seed.")
            print("   To re-seed, delete database.sqlite and run again.")
            db.close()
            return

        teams = seed_teams(db)
        matches = seed_matches(db, teams)
        update_team_form_from_friendly(db, teams, matches)
        seed_predictions(db, matches, teams)
        user = seed_user(db)
        seed_licenses(db)
        db.commit()

        # 统计
        wc_count = sum(1 for m in matches if m.match_type == MatchType.WORLD_CUP)
        friendly_count = sum(1 for m in matches if m.match_type == MatchType.FRIENDLY)
        finished_friendly = sum(1 for m in matches if m.match_type == MatchType.FRIENDLY and m.status == MatchStatus.FINISHED)

        print("\n" + "=" * 50)
        print("Seed complete!")
        print("=" * 50)
        print(f"\n数据概览:")
        print(f"  球队: {len(teams)} 支（48支世界杯参赛队）")
        print(f"  世界杯小组赛: {wc_count} 场")
        print(f"  热身赛/友谊赛: {friendly_count} 场（{finished_friendly} 场已结束，已更新球队状态）")
        print(f"\n你可以：")
        print(f"  1. 访问 http://localhost:8000/static/index.html")
        print(f"  2. 登录: test@example.com / test123")
        print(f"  3. 用卡密解锁完整功能")
        print(f"\n管理员后台:")
        print(f"  curl -H 'X-API-Key: <your-admin-key>' http://localhost:8000/api/admin/dashboard")
    finally:
        db.close()


if __name__ == "__main__":
    main()
