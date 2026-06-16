"""
真实友谊赛数据同步脚本
从外部数据源（Futbol24 等）同步 2026 世界杯前国际友谊赛赛程。

用法：
    cd backend && python sync_real_fixtures.py

行为：
    1. 删除旧的模拟友谊赛（FRIENDLY-01~08）
    2. 自动创建缺失的球队（使用占位数据）
    3. 插入真实的友谊赛赛程
    4. 为所有新增比赛生成预测快照
"""

from datetime import datetime, timezone, timedelta
from sqlalchemy.orm import Session

from database.config import get_settings
from database.models import (
    init_db, get_db, Team, Match, MatchStatus, MatchType,
    Prediction, PlayType
)
from core.prediction_engine import PredictionEngine, MatchContext, TeamContext
from utils.logger import get_logger

settings = get_settings()
logger = get_logger("sync")

# ────────────────────────────
# 缺失球队的占位数据（自动创建）
# ────────────────────────────
MISSING_TEAMS = [
    # 欧洲
    {"name": "爱尔兰", "name_en": "Ireland", "code": "IRL", "flag": "🇮🇪", "elo": 1650, "fifa_rank": 52, "continent": "Europe", "avg_goals_scored": 1.00, "avg_goals_conceded": 1.20, "form_factor": 0.95},
    {"name": "俄罗斯", "name_en": "Russia", "code": "RUS", "flag": "🇷🇺", "elo": 1700, "fifa_rank": 38, "continent": "Europe", "avg_goals_scored": 1.20, "avg_goals_conceded": 1.00, "form_factor": 0.98},
    {"name": "卡塔尔", "name_en": "Qatar", "code": "QAT", "flag": "🇶🇦", "elo": 1620, "fifa_rank": 58, "continent": "Asia", "avg_goals_scored": 0.90, "avg_goals_conceded": 1.40, "form_factor": 0.90},
    {"name": "安道尔", "name_en": "Andorra", "code": "AND", "flag": "🇦🇩", "elo": 1200, "fifa_rank": 170, "continent": "Europe", "avg_goals_scored": 0.30, "avg_goals_conceded": 2.00, "form_factor": 0.70},
    {"name": "波黑", "name_en": "Bosnia", "code": "BIH", "flag": "🇧🇦", "elo": 1680, "fifa_rank": 50, "continent": "Europe", "avg_goals_scored": 1.10, "avg_goals_conceded": 1.30, "form_factor": 0.95},
    {"name": "北马其顿", "name_en": "North Macedonia", "code": "MKD", "flag": "🇲🇰", "elo": 1600, "fifa_rank": 65, "continent": "Europe", "avg_goals_scored": 0.90, "avg_goals_conceded": 1.40, "form_factor": 0.92},
    {"name": "芬兰", "name_en": "Finland", "code": "FIN", "flag": "🇫🇮", "elo": 1660, "fifa_rank": 55, "continent": "Europe", "avg_goals_scored": 1.00, "avg_goals_conceded": 1.30, "form_factor": 0.94},
    {"name": "冰岛", "name_en": "Iceland", "code": "ISL", "flag": "🇮🇸", "elo": 1640, "fifa_rank": 60, "continent": "Europe", "avg_goals_scored": 0.90, "avg_goals_conceded": 1.40, "form_factor": 0.92},
    {"name": "新加坡", "name_en": "Singapore", "code": "SGP", "flag": "🇸🇬", "elo": 1350, "fifa_rank": 155, "continent": "Asia", "avg_goals_scored": 0.50, "avg_goals_conceded": 2.00, "form_factor": 0.75},
    {"name": "蒙古", "name_en": "Mongolia", "code": "MNG", "flag": "🇲🇳", "elo": 1150, "fifa_rank": 190, "continent": "Asia", "avg_goals_scored": 0.30, "avg_goals_conceded": 2.50, "form_factor": 0.65},
    {"name": "奥地利", "name_en": "Austria", "code": "AUT", "flag": "🇦🇹", "elo": 1750, "fifa_rank": 28, "continent": "Europe", "avg_goals_scored": 1.30, "avg_goals_conceded": 1.10, "form_factor": 0.98},
    {"name": "保加利亚", "name_en": "Bulgaria", "code": "BUL", "flag": "🇧🇬", "elo": 1580, "fifa_rank": 75, "continent": "Europe", "avg_goals_scored": 0.80, "avg_goals_conceded": 1.50, "form_factor": 0.88},
    {"name": "黑山", "name_en": "Montenegro", "code": "MNE", "flag": "🇲🇪", "elo": 1560, "fifa_rank": 80, "continent": "Europe", "avg_goals_scored": 0.80, "avg_goals_conceded": 1.40, "form_factor": 0.88},
    {"name": "斯洛伐克", "name_en": "Slovakia", "code": "SVK", "flag": "🇸🇰", "elo": 1680, "fifa_rank": 48, "continent": "Europe", "avg_goals_scored": 1.10, "avg_goals_conceded": 1.20, "form_factor": 0.96},
    {"name": "马耳他", "name_en": "Malta", "code": "MLT", "flag": "🇲🇹", "elo": 1300, "fifa_rank": 165, "continent": "Europe", "avg_goals_scored": 0.40, "avg_goals_conceded": 2.00, "form_factor": 0.72},
    {"name": "挪威", "name_en": "Norway", "code": "NOR", "flag": "🇳🇴", "elo": 1720, "fifa_rank": 40, "continent": "Europe", "avg_goals_scored": 1.30, "avg_goals_conceded": 1.00, "form_factor": 0.98},
    {"name": "瑞典", "name_en": "Sweden", "code": "SWE", "flag": "🇸🇪", "elo": 1700, "fifa_rank": 42, "continent": "Europe", "avg_goals_scored": 1.20, "avg_goals_conceded": 1.10, "form_factor": 0.96},
    {"name": "格鲁吉亚", "name_en": "Georgia", "code": "GEO", "flag": "🇬🇪", "elo": 1620, "fifa_rank": 70, "continent": "Europe", "avg_goals_scored": 0.90, "avg_goals_conceded": 1.30, "form_factor": 0.92},
    {"name": "罗马尼亚", "name_en": "Romania", "code": "ROU", "flag": "🇷🇴", "elo": 1660, "fifa_rank": 56, "continent": "Europe", "avg_goals_scored": 1.00, "avg_goals_conceded": 1.20, "form_factor": 0.94},
    {"name": "威尔士", "name_en": "Wales", "code": "WAL", "flag": "🏴󠁧󠁢󠁷󠁬󠁳󠁿", "elo": 1640, "fifa_rank": 62, "continent": "Europe", "avg_goals_scored": 0.90, "avg_goals_conceded": 1.30, "form_factor": 0.92},
    {"name": "白俄罗斯", "name_en": "Belarus", "code": "BLR", "flag": "🇧🇾", "elo": 1500, "fifa_rank": 95, "continent": "Europe", "avg_goals_scored": 0.70, "avg_goals_conceded": 1.50, "form_factor": 0.85},
    {"name": "朝鲜", "name_en": "North Korea", "code": "PRK", "flag": "🇰🇵", "elo": 1480, "fifa_rank": 110, "continent": "Asia", "avg_goals_scored": 0.60, "avg_goals_conceded": 1.80, "form_factor": 0.80},
    {"name": "卢森堡", "name_en": "Luxembourg", "code": "LUX", "flag": "🇱🇺", "elo": 1520, "fifa_rank": 90, "continent": "Europe", "avg_goals_scored": 0.70, "avg_goals_conceded": 1.60, "form_factor": 0.85},
    {"name": "萨尔瓦多", "name_en": "El Salvador", "code": "SLV", "flag": "🇸🇻", "elo": 1450, "fifa_rank": 115, "continent": "North America", "avg_goals_scored": 0.60, "avg_goals_conceded": 1.70, "form_factor": 0.82},
    {"name": "阿尔巴尼亚", "name_en": "Albania", "code": "ALB", "flag": "🇦🇱", "elo": 1600, "fifa_rank": 68, "continent": "Europe", "avg_goals_scored": 0.90, "avg_goals_conceded": 1.30, "form_factor": 0.92},
    {"name": "以色列", "name_en": "Israel", "code": "ISR", "flag": "🇮🇱", "elo": 1640, "fifa_rank": 63, "continent": "Europe", "avg_goals_scored": 1.00, "avg_goals_conceded": 1.30, "form_factor": 0.93},
    {"name": "捷克", "name_en": "Czech Republic", "code": "CZE", "flag": "🇨🇿", "elo": 1720, "fifa_rank": 41, "continent": "Europe", "avg_goals_scored": 1.20, "avg_goals_conceded": 1.00, "form_factor": 0.97},
    {"name": "危地马拉", "name_en": "Guatemala", "code": "GUA", "flag": "🇬🇹", "elo": 1420, "fifa_rank": 120, "continent": "North America", "avg_goals_scored": 0.60, "avg_goals_conceded": 1.80, "form_factor": 0.80},
    {"name": "北爱尔兰", "name_en": "Northern Ireland", "code": "NIR", "flag": "🇬🇧", "elo": 1550, "fifa_rank": 82, "continent": "Europe", "avg_goals_scored": 0.80, "avg_goals_conceded": 1.40, "form_factor": 0.88},
    {"name": "几内亚", "name_en": "Guinea", "code": "GUI", "flag": "🇬🇳", "elo": 1500, "fifa_rank": 98, "continent": "Africa", "avg_goals_scored": 0.70, "avg_goals_conceded": 1.50, "form_factor": 0.85},
    {"name": "斯洛文尼亚", "name_en": "Slovenia", "code": "SVN", "flag": "🇸🇮", "elo": 1660, "fifa_rank": 54, "continent": "Europe", "avg_goals_scored": 1.00, "avg_goals_conceded": 1.20, "form_factor": 0.95},
    {"name": "塞浦路斯", "name_en": "Cyprus", "code": "CYP", "flag": "🇨🇾", "elo": 1400, "fifa_rank": 125, "continent": "Europe", "avg_goals_scored": 0.50, "avg_goals_conceded": 1.80, "form_factor": 0.78},
    # 非洲
    {"name": "加纳", "name_en": "Ghana", "code": "GHA", "flag": "🇬🇭", "elo": 1670, "fifa_rank": 51, "continent": "Africa", "avg_goals_scored": 1.10, "avg_goals_conceded": 1.20, "form_factor": 0.95},
    {"name": "津巴布韦", "name_en": "Zimbabwe", "code": "ZIM", "flag": "🇿🇼", "elo": 1400, "fifa_rank": 130, "continent": "Africa", "avg_goals_scored": 0.60, "avg_goals_conceded": 1.60, "form_factor": 0.82},
    {"name": "南非", "name_en": "South Africa", "code": "RSA", "flag": "🇿🇦", "elo": 1550, "fifa_rank": 45, "continent": "Africa", "avg_goals_scored": 0.80, "avg_goals_conceded": 1.30, "form_factor": 0.92},
    {"name": "尼加拉瓜", "name_en": "Nicaragua", "code": "NCA", "flag": "🇳🇮", "elo": 1300, "fifa_rank": 160, "continent": "North America", "avg_goals_scored": 0.50, "avg_goals_conceded": 2.00, "form_factor": 0.75},
    {"name": "佛得角", "name_en": "Cape Verde", "code": "CPV", "flag": "🇨🇻", "elo": 1500, "fifa_rank": 100, "continent": "Africa", "avg_goals_scored": 0.70, "avg_goals_conceded": 1.40, "form_factor": 0.85},
    {"name": "刚果民主共和国", "name_en": "DR Congo", "code": "COD", "flag": "🇨🇩", "elo": 1520, "fifa_rank": 92, "continent": "Africa", "avg_goals_scored": 0.80, "avg_goals_conceded": 1.50, "form_factor": 0.86},
    # 北美/加勒比
    {"name": "格林纳达", "name_en": "Grenada", "code": "GRN", "flag": "🇬🇩", "elo": 1200, "fifa_rank": 175, "continent": "North America", "avg_goals_scored": 0.30, "avg_goals_conceded": 2.20, "form_factor": 0.68},
    {"name": "库拉索", "name_en": "Curacao", "code": "CUW", "flag": "🇨🇼", "elo": 1380, "fifa_rank": 140, "continent": "North America", "avg_goals_scored": 0.60, "avg_goals_conceded": 1.80, "form_factor": 0.80},
    {"name": "波多黎各", "name_en": "Puerto Rico", "code": "PUR", "flag": "🇵🇷", "elo": 1250, "fifa_rank": 168, "continent": "North America", "avg_goals_scored": 0.40, "avg_goals_conceded": 2.00, "form_factor": 0.72},
    {"name": "海地", "name_en": "Haiti", "code": "HAI", "flag": "🇭🇹", "elo": 1420, "fifa_rank": 118, "continent": "North America", "avg_goals_scored": 0.70, "avg_goals_conceded": 1.60, "form_factor": 0.83},
    {"name": "多米尼加共和国", "name_en": "Dominican Republic", "code": "DOM", "flag": "🇩🇴", "elo": 1320, "fifa_rank": 150, "continent": "North America", "avg_goals_scored": 0.50, "avg_goals_conceded": 1.90, "form_factor": 0.76},
    {"name": "巴拿马", "name_en": "Panama", "code": "PAN", "flag": "🇵🇦", "elo": 1540, "fifa_rank": 46, "continent": "North America", "avg_goals_scored": 0.70, "avg_goals_conceded": 1.50, "form_factor": 0.88},
    # 亚洲
    {"name": "印度", "name_en": "India", "code": "IND", "flag": "🇮🇳", "elo": 1350, "fifa_rank": 145, "continent": "Asia", "avg_goals_scored": 0.50, "avg_goals_conceded": 1.80, "form_factor": 0.78},
    {"name": "牙买加", "name_en": "Jamaica", "code": "JAM", "flag": "🇯🇲", "elo": 1500, "fifa_rank": 47, "continent": "North America", "avg_goals_scored": 0.60, "avg_goals_conceded": 1.60, "form_factor": 0.85},
    # 大洋洲
    {"name": "新西兰", "name_en": "New Zealand", "code": "NZL", "flag": "🇳🇿", "elo": 1520, "fifa_rank": 44, "continent": "Oceania", "avg_goals_scored": 0.70, "avg_goals_conceded": 1.60, "form_factor": 0.87},
    # 其他
    {"name": "列支敦士登", "name_en": "Liechtenstein", "code": "LIE", "flag": "🇱🇮", "elo": 1100, "fifa_rank": 200, "continent": "Europe", "avg_goals_scored": 0.20, "avg_goals_conceded": 2.50, "form_factor": 0.60},
    {"name": "柬埔寨", "name_en": "Cambodia", "code": "CAM", "flag": "🇰🇭", "elo": 1200, "fifa_rank": 180, "continent": "Asia", "avg_goals_scored": 0.40, "avg_goals_conceded": 2.20, "form_factor": 0.68},
    {"name": "不丹", "name_en": "Bhutan", "code": "BHU", "flag": "🇧🇹", "elo": 1050, "fifa_rank": 210, "continent": "Asia", "avg_goals_scored": 0.20, "avg_goals_conceded": 2.80, "form_factor": 0.55},
]

# ────────────────────────────
# 2026年5-6月真实国际友谊赛赛程（来源：Futbol24）
# 格式: (match_code, home_code, away_code, date_str, time_str, odds_tuple)
# ────────────────────────────
REAL_FRIENDLIES = [
    ("FR-2026-0516", "IRL", "GRN", "2026-05-16", "18:00", (1.25, 5.50, 12.00)),
    ("FR-2026-0522", "MEX", "GHA", "2026-05-22", "13:00", (1.80, 3.40, 4.50)),
    ("FR-2026-0526", "NGA", "ZIM", "2026-05-26", "20:30", (1.40, 4.20, 8.50)),
    ("FR-2026-0527", "JAM", "IND", "2026-05-27", "20:30", (1.60, 3.60, 5.80)),
    ("FR-2026-0528A", "EGY", "RUS", "2026-05-28", "13:00", (2.40, 3.20, 3.00)),
    ("FR-2026-0528B", "IRL", "QAT", "2026-05-28", "13:00", (1.90, 3.40, 4.00)),
    ("FR-2026-0529A", "AND", "IRQ", "2026-05-29", "13:00", (6.50, 4.20, 1.45)),
    ("FR-2026-0529B", "BIH", "MKD", "2026-05-29", "13:00", (2.10, 3.30, 3.40)),
    ("FR-2026-0529C", "COL", "CRC", "2026-05-29", "13:00", (1.55, 3.80, 6.20)),
    ("FR-2026-0529D", "RSA", "NCA", "2026-05-29", "13:00", (1.45, 4.00, 7.50)),
    ("FR-2026-0530A", "MEX", "AUS", "2026-05-30", "13:00", (1.75, 3.50, 4.80)),
    ("FR-2026-0530B", "SCO", "CUW", "2026-05-30", "13:00", (1.35, 4.80, 8.50)),
    ("FR-2026-0530C", "ECU", "KSA", "2026-05-30", "01:30", (1.70, 3.60, 5.00)),
    ("FR-2026-0531A", "CPV", "SRB", "2026-05-31", "13:00", (3.80, 3.40, 1.95)),
    ("FR-2026-0531B", "GER", "FIN", "2026-05-31", "13:00", (1.25, 5.50, 12.00)),
    ("FR-2026-0531C", "JPN", "ISL", "2026-05-31", "13:00", (1.45, 4.20, 7.00)),
    ("FR-2026-0531D", "SGP", "MNG", "2026-05-31", "13:00", (1.50, 4.00, 6.50)),
    ("FR-2026-0531E", "USA", "SEN", "2026-05-31", "13:00", (1.85, 3.40, 4.20)),
    ("FR-2026-0531F", "SUI", "JOR", "2026-05-31", "15:00", (1.40, 4.50, 8.00)),
    ("FR-2026-0531G", "BRA", "PAN", "2026-05-31", "20:00", (1.20, 6.00, 15.00)),
    ("FR-2026-0531H", "POL", "UKR", "2026-05-31", "20:45", (1.90, 3.40, 4.00)),
    ("FR-2026-0601A", "AUT", "TUN", "2026-06-01", "13:00", (1.55, 3.80, 6.00)),
    ("FR-2026-0601B", "CAN", "UZB", "2026-06-01", "13:00", (1.60, 3.70, 5.50)),
    ("FR-2026-0601C", "BUL", "MNE", "2026-06-01", "18:00", (2.10, 3.20, 3.50)),
    ("FR-2026-0601D", "SVK", "MLT", "2026-06-01", "18:00", (1.30, 5.00, 10.00)),
    ("FR-2026-0601E", "NOR", "SWE", "2026-06-01", "19:00", (1.80, 3.50, 4.40)),
    ("FR-2026-0601F", "TUR", "MKD", "2026-06-01", "20:30", (1.45, 4.20, 7.00)),
    ("FR-2026-0602A", "GEO", "ROU", "2026-06-02", "13:00", (2.30, 3.20, 3.10)),
    ("FR-2026-0602B", "RSA", "PUR", "2026-06-02", "13:00", (1.35, 4.50, 9.00)),
    ("FR-2026-0602C", "WAL", "GHA", "2026-06-02", "13:00", (2.00, 3.30, 3.70)),
    ("FR-2026-0602D", "CRO", "BEL", "2026-06-02", "18:00", (2.40, 3.30, 2.90)),
    ("FR-2026-0602E", "HAI", "NZL", "2026-06-02", "01:30", (2.80, 3.20, 2.50)),
    ("FR-2026-0603A", "BLR", "PRK", "2026-06-03", "13:00", (2.20, 3.10, 3.40)),
    ("FR-2026-0603B", "LUX", "ITA", "2026-06-03", "13:00", (8.00, 4.50, 1.35)),
    ("FR-2026-0603C", "POL", "NGA", "2026-06-03", "13:00", (1.75, 3.50, 4.80)),
    ("FR-2026-0603D", "KOR", "SLV", "2026-06-03", "13:00", (1.30, 5.00, 10.00)),
    ("FR-2026-0603E", "ALB", "ISR", "2026-06-03", "20:00", (2.40, 3.20, 3.00)),
    ("FR-2026-0603F", "NED", "ALG", "2026-06-03", "20:45", (1.40, 4.50, 8.00)),
    ("FR-2026-0603G", "DEN", "COD", "2026-06-03", "21:00", (1.35, 4.80, 9.00)),
    ("FR-2026-0604A", "PAN", "DOM", "2026-06-04", "02:45", (1.45, 4.20, 7.00)),
    ("FR-2026-0604B", "AND", "LIE", "2026-06-04", "13:00", (2.80, 3.20, 2.50)),
    ("FR-2026-0604C", "CAM", "BHU", "2026-06-04", "13:00", (1.55, 3.80, 6.00)),
    ("FR-2026-0604D", "CZE", "GUA", "2026-06-04", "13:00", (1.30, 5.00, 10.00)),
    ("FR-2026-0604E", "FRA", "CIV", "2026-06-04", "13:00", (1.30, 5.50, 10.00)),
    ("FR-2026-0604F", "MEX", "SRB", "2026-06-04", "13:00", (1.80, 3.40, 4.50)),
    ("FR-2026-0604G", "NIR", "GUI", "2026-06-04", "13:00", (1.70, 3.50, 5.00)),
    ("FR-2026-0604H", "SVN", "CYP", "2026-06-04", "13:00", (1.40, 4.50, 8.00)),
    ("FR-2026-0604I", "ESP", "IRQ", "2026-06-04", "13:00", (1.25, 5.50, 12.00)),
]


def ensure_teams(db: Session) -> dict[str, Team]:
    """确保所有需要的球队都存在于数据库中"""
    team_map: dict[str, Team] = {}

    # 先加载现有球队
    existing = db.query(Team).all()
    for t in existing:
        team_map[t.code] = t

    # 创建缺失的球队
    created = 0
    for data in MISSING_TEAMS:
        if data["code"] not in team_map:
            t = Team(**data)
            db.add(t)
            team_map[data["code"]] = t
            created += 1

    if created:
        db.commit()
        for t in list(team_map.values())[-created:]:
            db.refresh(t)
        logger.info(f"Created {created} missing teams")
        print(f"✅ Created {created} missing teams")
    else:
        print("✅ All teams already exist")

    return team_map


def delete_fake_friendlies(db: Session):
    """删除旧的模拟友谊赛（FRIENDLY-01~08）及其预测"""
    fake_codes = [f"FRIENDLY-{i:02d}" for i in range(1, 9)]

    # 查找要删除的比赛
    to_delete = db.query(Match).filter(Match.match_code.in_(fake_codes)).all()
    if not to_delete:
        print("✅ No fake friendlies to delete")
        return

    match_ids = [m.id for m in to_delete]

    # 先删除关联的预测
    pred_count = db.query(Prediction).filter(Prediction.match_id.in_(match_ids)).delete(synchronize_session=False)

    # 再删除比赛
    match_count = db.query(Match).filter(Match.match_code.in_(fake_codes)).delete(synchronize_session=False)

    db.commit()
    logger.info(f"Deleted {match_count} fake friendlies and {pred_count} predictions")
    print(f"✅ Deleted {match_count} fake friendlies and {pred_count} predictions")


def create_real_friendlies(db: Session, team_map: dict[str, Team]) -> list[Match]:
    """创建真实的友谊赛记录（幂等：已存在则跳过）"""
    existing_codes = {
        row[0] for row in db.query(Match.match_code).filter(
            Match.match_code.like("FR-2026-%")
        ).all()
    }

    matches = []
    skipped = 0

    for code, home_code, away_code, date_str, time_str, odds in REAL_FRIENDLIES:
        if code in existing_codes:
            skipped += 1
            continue

        home = team_map.get(home_code)
        away = team_map.get(away_code)
        if not home or not away:
            logger.warning(f"Skipping {code}: team not found ({home_code} vs {away_code})")
            continue

        # 解析开球时间（假设为 UTC，然后转北京时间显示）
        # Futbol24 的时间通常是当地时间，这里简化处理：统一当作 UTC+0
        kickoff_str = f"{date_str}T{time_str}:00"
        try:
            kickoff = datetime.strptime(kickoff_str, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
        except ValueError:
            # 如果时间是 01:30 等凌晨场次，可能是次日当地时间的比赛
            kickoff = datetime.strptime(kickoff_str, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)

        m = Match(
            match_code=code,
            home_team_id=home.id,
            away_team_id=away.id,
            kickoff_at=kickoff,
            group=None,
            stage="friendly",
            match_type=MatchType.FRIENDLY,
            competition="International Friendly",
            status=MatchStatus.SCHEDULED,
            odds_home=odds[0],
            odds_draw=odds[1],
            odds_away=odds[2],
        )
        db.add(m)
        matches.append(m)

    db.commit()
    for m in matches:
        db.refresh(m)

    logger.info(f"Created {len(matches)} real friendlies, skipped {skipped}")
    print(f"✅ Created {len(matches)} real friendlies ({skipped} already existed)")
    return matches


def generate_predictions(db: Session, matches: list[Match], team_map: dict[str, Team]):
    """为新增比赛生成预测快照"""
    if not matches:
        print("✅ No new matches to predict")
        return

    engine = PredictionEngine()
    created = 0

    for match in matches:
        home = team_map.get(match.home_team_id)
        away = team_map.get(match.away_team_id)
        if not home or not away:
            continue

        ctx = MatchContext(
            match_id=match.id,
            home_team=TeamContext(
                team_id=home.id,
                name=home.name,
                elo=home.elo or 1600,
                fifa_rank=home.fifa_rank or 100,
                avg_goals_scored=home.avg_goals_scored or 1.0,
                avg_goals_conceded=home.avg_goals_conceded or 1.2,
                form_factor=home.form_factor or 1.0,
                recent_results=home.recent_results or "",
                recent_goals_scored=home.recent_goals_scored or 0,
                recent_goals_conceded=home.recent_goals_conceded or 0,
                home_away_factor=home.home_away_factor or 1.0,
                weather_adaptability=home.weather_adaptability or 1.0,
                tactical_style=home.tactical_style or "balanced",
                coach_rating=home.coach_rating or 0.5,
                rest_days=home.rest_days or 7,
                key_injuries=home.key_injuries or "",
                squad_fatigue_index=home.squad_fatigue_index or 0.5,
            ),
            away_team=TeamContext(
                team_id=away.id,
                name=away.name,
                elo=away.elo or 1600,
                fifa_rank=away.fifa_rank or 100,
                avg_goals_scored=away.avg_goals_scored or 1.0,
                avg_goals_conceded=away.avg_goals_conceded or 1.2,
                form_factor=away.form_factor or 1.0,
                recent_results=away.recent_results or "",
                recent_goals_scored=away.recent_goals_scored or 0,
                recent_goals_conceded=away.recent_goals_conceded or 0,
                home_away_factor=away.home_away_factor or 1.0,
                weather_adaptability=away.weather_adaptability or 1.0,
                tactical_style=away.tactical_style or "balanced",
                coach_rating=away.coach_rating or 0.5,
                rest_days=away.rest_days or 7,
                key_injuries=away.key_injuries or "",
                squad_fatigue_index=away.squad_fatigue_index or 0.5,
            ),
            stage="friendly",
            is_knockout=False,
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
                # 修复 (2026-06-17): 统一为 "v2.0"
                model_version="v2.0",
            )
            db.add(pred)
            created += 1

        match.confidence = result.confidence

    db.commit()
    logger.info(f"Created {created} predictions for {len(matches)} matches")
    print(f"✅ Created {created} predictions for {len(matches)} matches")


def migrate_db():
    """SQLite 简单迁移：添加缺失的列"""
    import sqlite3
    from pathlib import Path
    db_path = str(settings.DATABASE_URL).replace("sqlite:///./", "").replace("sqlite:///", "")
    conn = sqlite3.connect(Path(__file__).parent / db_path)
    cursor = conn.cursor()

    # 检查 matches 表是否有 confidence 列
    cursor.execute("PRAGMA table_info(matches)")
    columns = [row[1] for row in cursor.fetchall()]
    if "confidence" not in columns:
        cursor.execute("ALTER TABLE matches ADD COLUMN confidence VARCHAR(10)")
        conn.commit()
        print("✅ Added 'confidence' column to matches table")

    conn.close()


def main():
    print("=" * 60)
    print("WC Analytics — 真实友谊赛数据同步")
    print("=" * 60)

    init_db()
    migrate_db()
    db = next(get_db())

    # 1. 确保所有球队存在
    team_map = ensure_teams(db)

    # 2. 删除旧模拟数据
    delete_fake_friendlies(db)

    # 3. 创建真实友谊赛
    new_matches = create_real_friendlies(db, team_map)

    # 4. 生成预测
    # 需要刷新 team_map（因为可能新增了球队）
    generate_predictions(db, new_matches, {t.id: t for t in db.query(Team).all()})

    # 统计
    total_friendly = db.query(Match).filter(Match.match_type == MatchType.FRIENDLY).count()
    total_wc = db.query(Match).filter(Match.match_type == MatchType.WORLD_CUP).count()
    total_teams = db.query(Team).count()

    print("\n" + "=" * 60)
    print("Sync complete!")
    print("=" * 60)
    print(f"\n数据概览:")
    print(f"  球队总数: {total_teams} 支")
    print(f"  世界杯比赛: {total_wc} 场")
    print(f"  真实友谊赛: {total_friendly} 场")
    print(f"\n提示:")
    print(f"  1. 访问 http://localhost:8000/static/index.html")
    print(f"  2. 切换到「热身赛」Tab 查看真实赛程")
    print(f"  3. 赛后用 Admin API 录入实际比分，验证看板自动更新")


if __name__ == "__main__":
    main()
