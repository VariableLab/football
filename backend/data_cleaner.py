"""
数据清洗模块 — 存量清洗 + 入口校验。

六大功能:
1. 时区统一: naive UTC → aware UTC
2. OddsHistory 去重: 同 match_id/source/5min 窗口只保留一条
3. 队名规范化: 统一别名映射
4. match_code / odds_source 受控词表
5. _safe_float 修复检查: 0.0 赔率标记为异常
6. Enum 一致性: raw string → enum value

用法:
    from data_cleaner import DataCleaner
    cleaner = DataCleaner(db)

    # 审计 (只读)
    report = cleaner.audit()

    # 清洗 (写入)
    result = cleaner.clean(dry_run=True)   # 预览
    result = cleaner.clean(dry_run=False)  # 执行
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Any

from sqlalchemy import func, text
from models import (
    Match, MatchStatus, MatchType,
    Team, OddsHistory, Prediction, PlayType,
    JingcaiIssue,
)

logger = logging.getLogger(__name__)

# ─── 受控词表 ───

VALID_ODDS_SOURCES = frozenset({
    "sporttery", "jingcai", "zgzcw", "500",
    "football-data-B365", "football-data-PS", "football-data-PH",
    "betexplorer", "oddsapi", "synthetic",
    "soccerdata", "combined", "opening",
})

VALID_MATCH_CODE_PREFIXES = frozenset({
    "JC",   # sporttery / jingcai
    "INT",  # historical international
    "OF",   # openfootball
    "FR",   # friendly / real fixtures
    "WC",   # world cup
})

VALID_PLAY_TYPES = frozenset({"SPF", "RQ", "SCORE", "GOALS", "HALF"})

# ─── 队名别名映射 (合并 zgzcw + 500.com + 常见变体) ───

TEAM_ALIASES: Dict[str, str] = {
    # 中文别名 → 规范名
    "巴黎圣日尔曼": "巴黎圣日耳曼",
    "巴黎": "巴黎圣日耳曼",
    "PSG": "巴黎圣日耳曼",
    "巴塞隆拿": "巴塞罗那",
    "巴萨": "巴塞罗那",
    "Barcelona": "巴塞罗那",
    "皇马": "皇家马德里",
    "利雅胜利": "利雅得胜利",
    "利雅新月": "利雅得新月",
    "利物浦": "利物浦",
    "车路士": "切尔西",
    "切尔西": "切尔西",
    "曼城": "曼彻斯特城",
    "曼聯": "曼彻斯特联",
    "曼联": "曼彻斯特联",
    "阿仙奴": "阿森纳",
    "阿森纳": "阿森纳",
    "热刺": "托特纳姆热刺",
    "托特纳姆": "托特纳姆热刺",
    "拜仁": "拜仁慕尼黑",
    "多特": "多特蒙德",
    "尤文": "尤文图斯",
    "祖云达斯": "尤文图斯",
    "国米": "国际米兰",
    "米兰": "AC米兰",
    "AC米蘭": "AC米兰",
    "马体会": "马德里竞技",
    "马竞": "马德里竞技",
    "车仔": "切尔西",
    "圣日门": "巴黎圣日耳曼",
    "修咸顿": "南安普顿",
    "南安普顿": "南安普顿",
    "修咸頓": "南安普顿",
    "纽卡素": "纽卡斯尔联",
    "纽卡斯尔": "纽卡斯尔联",
    "阿斯顿维拉": "阿斯顿维拉",
    "阿士东维拉": "阿斯顿维拉",
    "白礼顿": "布莱顿",
    "布莱顿": "布莱顿",
    "水晶宫": "水晶宫",
    "富勒姆": "富勒姆",
    "富咸": "富勒姆",
    "狼队": "伍尔弗汉普顿",
    "狼隊": "伍尔弗汉普顿",
    "爱华顿": "埃弗顿",
    "埃弗顿": "埃弗顿",
    "诺丁汉": "诺丁汉森林",
    "般尼": "伯恩利",
    "伯恩利": "伯恩利",
    "西布朗": "西布罗姆维奇",
    "列斯联": "利兹联",
    "利兹联": "利兹联",
    "贝迪斯": "皇家贝蒂斯",
    "贝蒂斯": "皇家贝蒂斯",
    "切尔达": "塞尔塔",
    "塞尔塔": "塞尔塔",
    "维拉利尔": "比利亚雷亚尔",
    "比利亚雷亚尔": "比利亚雷亚尔",
    "塞维利亚": "塞维利亚",
    "西维尔": "塞维利亚",
    "华伦西亚": "巴伦西亚",
    "巴伦西亚": "巴伦西亚",
    "皇家苏斯达": "皇家社会",
    "皇家社会": "皇家社会",
    "拉科鲁尼亚": "拉科鲁尼亚",
    "赫罗纳": "赫罗纳",
    "基罗纳": "赫罗纳",
    "马略卡": "马略卡",
    "奥萨苏纳": "奥萨苏纳",
    "拉斯彭马斯": "拉斯帕尔马斯",
    "巴列卡诺": "巴列卡诺",
    "艾尔切": "埃尔切",
    "埃尔切": "埃尔切",
    "加的斯": "加的斯",
    "阿尔梅里亚": "阿尔梅里亚",
    "罗马": "罗马",
    "拿玻里": "那不勒斯",
    "那不勒斯": "那不勒斯",
    "亚特兰大": "亚特兰大",
    "拉齐奥": "拉齐奥",
    "拉素": "拉齐奥",
    "佛罗伦萨": "佛罗伦萨",
    "费伦天拿": "佛罗伦萨",
    "博洛尼亚": "博洛尼亚",
    "博洛尼亞": "博洛尼亚",
    "都灵": "都灵",
    "蒙扎": "蒙扎",
    "萨索洛": "萨索洛",
    "乌迪内斯": "乌迪内斯",
    "莱切": "莱切",
    "维罗纳": "维罗纳",
    "卡利亚里": "卡利亚里",
    "恩波利": "恩波利",
    "弗洛西诺内": "弗洛西诺内",
    "萨勒尼塔纳": "萨勒尼塔纳",
    "圣旺红星": "圣旺红星",
    "罗德兹": "罗德兹",
    "神户胜利": "神户胜利船",
    "京都": "京都不死鸟",
    "富川FC": "富川FC",
    "全北现代": "全北现代",
    "南安普敦": "南安普顿",
    "米堡": "米德尔斯堡",
}


def resolve_team_name(raw: str) -> str:
    """将原始队名解析为规范名。"""
    if not raw:
        return raw
    stripped = raw.strip()
    return TEAM_ALIASES.get(stripped, stripped)


def resolve_team_db(db, raw_name: str) -> Optional[int]:
    """
    根据原始队名在 DB 中查找 Team.id。
    查找顺序: 规范名精确匹配 → name_en 精确匹配 → code 精确匹配 → 规范名子串匹配。
    返回 Team.id 或 None。
    """
    canonical = resolve_team_name(raw_name)
    team = db.query(Team).filter(Team.name == canonical).first()
    if team:
        return team.id
    team = db.query(Team).filter(Team.name_en == raw_name).first()
    if team:
        return team.id
    team = db.query(Team).filter(Team.code == raw_name.upper()[:3]).first()
    if team:
        return team.id
    # 最后尝试子串匹配 (name_en), 最小5字符
    if len(raw_name) >= 5:
        team = db.query(Team).filter(Team.name_en.ilike(f"%{raw_name}%")).first()
        if team:
            return team.id
    return None


# ─── 预写入校验 (所有导入路径调用) ───

BJ_TZ = timezone(timedelta(hours=8))
UTC_TZ = timezone.utc

ODDS_MIN = 1.01
ODDS_MAX = 100.0


def validate_odds(home: Optional[float], draw: Optional[float], away: Optional[float]) -> tuple:
    """
    校验赔率三元组。返回 (home, draw, away, valid)。
    - None 或 <= 0 → None
    - 超出 [1.01, 100] → None
    - 合法值原样返回
    """
    def _check(v):
        if v is None:
            return None
        try:
            f = float(v)
            return f if ODDS_MIN <= f <= ODDS_MAX else None
        except (ValueError, TypeError):
            return None

    h, d, a = _check(home), _check(draw), _check(away)
    valid = h is not None and d is not None and a is not None
    return h, d, a, valid


def ensure_aware_utc(dt: Optional[datetime], assume_bj: bool = True) -> Optional[datetime]:
    """
    确保返回 timezone-aware UTC datetime。
    - None → None
    - 已有时区 → 转为 UTC
    - naive → 如果 assume_bj=True 视为北京时间(UTC+8)再转UTC，否则视为UTC
    """
    if dt is None:
        return None
    if dt.tzinfo is not None:
        return dt.astimezone(UTC_TZ)
    # naive datetime: 默认视为北京时间 (sporttery 数据源)
    if assume_bj:
        dt = dt.replace(tzinfo=BJ_TZ)
        return dt.astimezone(UTC_TZ)
    return dt.replace(tzinfo=UTC_TZ)


def validate_source(source: str) -> str:
    """校验 odds_source，返回规范名。未知 source 返回 'unknown'。"""
    if source in VALID_ODDS_SOURCES:
        return source
    SOURCE_MAP = {
        "football-data": "football-data-B365",
        "fd-B365": "football-data-B365",
        "fd-PS": "football-data-PS",
        "fd-PH": "football-data-PH",
        "football-data-BW": "football-data-B365",
        "manual": "sporttery",
    }
    return SOURCE_MAP.get(source, "unknown")


# ─── 数据结构 ───

@dataclass
class AuditFinding:
    category: str
    severity: str  # "critical" | "warning" | "info"
    table: str
    count: int
    description: str
    fixable: bool = False


@dataclass
class CleanResult:
    dry_run: bool
    findings: List[AuditFinding] = field(default_factory=list)
    fixed: Dict[str, int] = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)


class DataCleaner:
    """数据清洗核心类。"""

    def __init__(self, db):
        self.db = db

    # ─── 审计 (只读) ───

    def audit(self) -> List[AuditFinding]:
        findings: List[AuditFinding] = []
        findings.extend(self._audit_timezone())
        findings.extend(self._audit_odds_duplicates())
        findings.extend(self._audit_zero_odds())
        findings.extend(self._audit_match_code_format())
        findings.extend(self._audit_odds_source())
        findings.extend(self._audit_enum_consistency())
        findings.extend(self._audit_team_names())
        findings.extend(self._audit_missing_fields())
        return findings

    def clean(self, dry_run: bool = True) -> CleanResult:
        result = CleanResult(dry_run=dry_run)

        result.findings = self.audit()

        # 1. 时区修复
        tz_fixed = self._fix_timezone(dry_run)
        result.fixed["timezone"] = tz_fixed

        # 2. OddsHistory 去重
        dedup_fixed = self._fix_odds_duplicates(dry_run)
        result.fixed["odds_dedup"] = dedup_fixed

        # 3. 0.0 赔率清理
        zero_fixed = self._fix_zero_odds(dry_run)
        result.fixed["zero_odds"] = zero_fixed

        # 4. odds_source 规范化
        source_fixed = self._fix_odds_source(dry_run)
        result.fixed["odds_source"] = source_fixed

        # 5. Enum 修复
        enum_fixed = self._fix_enum_consistency(dry_run)
        result.fixed["enum"] = enum_fixed

        if not dry_run:
            try:
                self.db.commit()
            except Exception as e:
                self.db.rollback()
                result.errors.append(f"Commit failed: {e}")

        return result

    # ─── 审计方法 ───

    def _audit_timezone(self) -> List[AuditFinding]:
        findings = []
        # SQLite strips tzinfo on round-trip — all datetimes appear naive.
        # We cannot reliably distinguish "stored as UTC" from "stored as Beijing time"
        # just by looking at the hour value. Instead, check for the sporttery
        # data source matches that should have been converted.
        # The display layer (MatchOut.kickoff_bj + app.js fmtBJ) handles both cases.
        findings.append(AuditFinding(
            category="timezone", severity="info",
            table="matches", count=0,
            description="SQLite 不保留 tzinfo，新数据通过 ensure_aware_utc 规范化，显示层已处理时区转换",
            fixable=False,
        ))
        naive_odds = self.db.query(OddsHistory).filter(
            text("recorded_at = datetime(recorded_at)")
        ).count()
        if naive_odds > 0:
            findings.append(AuditFinding(
                category="timezone", severity="warning",
                table="odds_history", count=naive_odds,
                description=f"{naive_odds} 条赔率记录 recorded_at 为 naive datetime",
                fixable=True,
            ))
        return findings

    def _audit_odds_duplicates(self) -> List[AuditFinding]:
        findings = []
        dup_count = self.db.execute(text("""
            SELECT COUNT(*) FROM (
                SELECT match_id, source,
                       ROUND(JULIANDAY(recorded_at)*288) as slot,
                       COUNT(*) as cnt
                FROM odds_history
                GROUP BY match_id, source, slot
                HAVING cnt > 1
            )
        """)).scalar() or 0
        if dup_count > 0:
            findings.append(AuditFinding(
                category="odds_dedup", severity="warning",
                table="odds_history", count=dup_count,
                description=f"{dup_count} 组赔率重复 (同 match_id/source/5min)",
                fixable=True,
            ))
        return findings

    def _audit_zero_odds(self) -> List[AuditFinding]:
        findings = []
        zero_matches = self.db.query(Match).filter(
            (Match.odds_home == 0) | (Match.odds_draw == 0) | (Match.odds_away == 0)
        ).count()
        if zero_matches > 0:
            findings.append(AuditFinding(
                category="zero_odds", severity="critical",
                table="matches", count=zero_matches,
                description=f"{zero_matches} 场比赛存在 0.0 赔率 (无效值)",
                fixable=True,
            ))
        zero_history = self.db.query(OddsHistory).filter(
            (OddsHistory.odds_home == 0) | (OddsHistory.odds_draw == 0) | (OddsHistory.odds_away == 0)
        ).count()
        if zero_history > 0:
            findings.append(AuditFinding(
                category="zero_odds", severity="warning",
                table="odds_history", count=zero_history,
                description=f"{zero_history} 条赔率记录存在 0.0 值",
                fixable=True,
            ))
        return findings

    def _audit_match_code_format(self) -> List[AuditFinding]:
        findings = []
        all_matches = self.db.query(Match.match_code).all()
        bad_codes = []
        for (code,) in all_matches:
            if not code:
                bad_codes.append(code)
                continue
            prefix = code.split("-")[0] if "-" in code else code[:2]
            if prefix not in VALID_MATCH_CODE_PREFIXES:
                bad_codes.append(code)
        if bad_codes:
            findings.append(AuditFinding(
                category="match_code", severity="info",
                table="matches", count=len(bad_codes),
                description=f"{len(bad_codes)} 个 match_code 前缀不在受控词表中: {bad_codes[:5]}",
                fixable=False,
            ))
        return findings

    def _audit_odds_source(self) -> List[AuditFinding]:
        findings = []
        invalid = self.db.query(Match.odds_source).filter(
            Match.odds_source != None,
            ~Match.odds_source.in_(VALID_ODDS_SOURCES),
        ).distinct().all()
        invalid_sources = [s for (s,) in invalid]
        if invalid_sources:
            findings.append(AuditFinding(
                category="odds_source", severity="warning",
                table="matches", count=len(invalid_sources),
                description=f"未知 odds_source: {invalid_sources[:10]}",
                fixable=True,
            ))

        hist_invalid = self.db.query(OddsHistory.source).filter(
            ~OddsHistory.source.in_(VALID_ODDS_SOURCES),
        ).distinct().all()
        hist_sources = [s for (s,) in hist_invalid]
        if hist_sources:
            findings.append(AuditFinding(
                category="odds_source", severity="info",
                table="odds_history", count=len(hist_sources),
                description=f"历史表未知 source: {hist_sources[:10]}",
                fixable=False,
            ))
        return findings

    def _audit_enum_consistency(self) -> List[AuditFinding]:
        findings = []
        valid_statuses = {s.value for s in MatchStatus}
        invalid_status = self.db.query(Match).filter(
            Match.status != None,
            ~Match.status.in_(valid_statuses),
        ).count()
        if invalid_status > 0:
            findings.append(AuditFinding(
                category="enum", severity="critical",
                table="matches", count=invalid_status,
                description=f"{invalid_status} 条比赛 status 不在枚举值中",
                fixable=True,
            ))

        valid_types = {t.value for t in MatchType}
        invalid_type = self.db.query(Match).filter(
            Match.match_type != None,
            ~Match.match_type.in_(valid_types),
        ).count()
        if invalid_type > 0:
            findings.append(AuditFinding(
                category="enum", severity="warning",
                table="matches", count=invalid_type,
                description=f"{invalid_type} 条比赛 match_type 不在枚举值中",
                fixable=True,
            ))

        invalid_pt = self.db.query(Prediction).filter(
            Prediction.play_type != None,
            ~Prediction.play_type.in_(VALID_PLAY_TYPES),
        ).count()
        if invalid_pt > 0:
            findings.append(AuditFinding(
                category="enum", severity="warning",
                table="predictions", count=invalid_pt,
                description=f"{invalid_pt} 条预测 play_type 不在受控词表中",
                fixable=True,
            ))
        return findings

    def _audit_team_names(self) -> List[AuditFinding]:
        findings = []
        all_teams = self.db.query(Team.name).all()
        aliased = []
        for (name,) in all_teams:
            if name in TEAM_ALIASES:
                aliased.append(name)
        if aliased:
            findings.append(AuditFinding(
                category="team_name", severity="warning",
                table="teams", count=len(aliased),
                description=f"{len(aliased)} 个队名是别名 (非规范名): {aliased[:5]}",
                fixable=True,
            ))
        return findings

    def _audit_missing_fields(self) -> List[AuditFinding]:
        findings = []
        no_kickoff = self.db.query(Match).filter(
            Match.kickoff_at == None,
            Match.status.in_(["upcoming", "scheduled"]),
        ).count()
        if no_kickoff > 0:
            findings.append(AuditFinding(
                category="missing", severity="warning",
                table="matches", count=no_kickoff,
                description=f"{no_kickoff} 场即将进行的比赛缺少 kickoff_at",
                fixable=False,
            ))
        no_odds = self.db.query(Match).filter(
            Match.status.in_(["upcoming", "scheduled"]),
            (Match.odds_home == None) | (Match.odds_home == 0),
        ).count()
        if no_odds > 0:
            findings.append(AuditFinding(
                category="missing", severity="info",
                table="matches", count=no_odds,
                description=f"{no_odds} 场即将进行的比赛缺少赔率",
                fixable=False,
            ))
        return findings

    # ─── 修复方法 ───

    def _fix_timezone(self, dry_run: bool) -> int:
        """将 naive datetime 统一为 UTC。
        只有 sporttery 数据 (match_code 以 JC 开头) 视为北京时间;
        其余所有前缀视为 UTC。
        """
        fixed = 0
        matches = self.db.query(Match).filter(
            Match.kickoff_at != None,
        ).all()
        for m in matches:
            if m.kickoff_at and m.kickoff_at.tzinfo is None:
                code = m.match_code or ""
                is_bj = code.startswith("JC")
                if not dry_run:
                    m.kickoff_at = ensure_aware_utc(m.kickoff_at, assume_bj=is_bj)
                fixed += 1
        if not dry_run and fixed > 0:
            self.db.flush()
        logger.info(f"[cleaner:timezone] {'[DRY-RUN] ' if dry_run else ''}Fixed {fixed} naive kickoff_at")
        return fixed

    def _fix_odds_duplicates(self, dry_run: bool) -> int:
        """删除 OddsHistory 重复行 (同 match_id/source/5min 窗口只保留最新一条)。"""
        dup_ids = self.db.execute(text("""
            SELECT id FROM (
                SELECT id, match_id, source,
                       ROUND(JULIANDAY(recorded_at)*288) as slot,
                       ROW_NUMBER() OVER (
                           PARTITION BY match_id, source,
                           ROUND(JULIANDAY(recorded_at)*288)
                           ORDER BY recorded_at DESC, id DESC
                       ) as rn
                FROM odds_history
            ) sub
            WHERE rn > 1
        """)).fetchall()

        delete_ids = [row[0] for row in dup_ids]
        count = len(delete_ids)
        if count > 0 and not dry_run:
            # Batch delete to avoid too large IN clause
            batch_size = 500
            for i in range(0, count, batch_size):
                batch = delete_ids[i:i + batch_size]
                self.db.query(OddsHistory).filter(
                    OddsHistory.id.in_(batch)
                ).delete(synchronize_session=False)
            self.db.flush()
        logger.info(f"[cleaner:dedup] {'[DRY-RUN] ' if dry_run else ''}Would delete {count} duplicate OddsHistory rows")
        return count

    def _fix_zero_odds(self, dry_run: bool) -> int:
        """将 0.0 赔率设为 None (无效标记)。"""
        fixed = 0
        matches = self.db.query(Match).filter(
            (Match.odds_home == 0) | (Match.odds_draw == 0) | (Match.odds_away == 0)
        ).all()
        for m in matches:
            changed = False
            if m.odds_home == 0:
                if not dry_run:
                    m.odds_home = None
                changed = True
            if m.odds_draw == 0:
                if not dry_run:
                    m.odds_draw = None
                changed = True
            if m.odds_away == 0:
                if not dry_run:
                    m.odds_away = None
                changed = True
            if changed:
                fixed += 1
        if not dry_run and fixed > 0:
            self.db.flush()
        logger.info(f"[cleaner:zero_odds] {'[DRY-RUN] ' if dry_run else ''}Fixed {fixed} matches with 0.0 odds")
        return fixed

    def _fix_odds_source(self, dry_run: bool) -> int:
        """将旧版 odds_source 映射到受控词表。"""
        SOURCE_MAP = {
            "football-data": "football-data-B365",
            "fd-B365": "football-data-B365",
            "fd-PS": "football-data-PS",
            "fd-PH": "football-data-PH",
        }
        fixed = 0
        for old, new in SOURCE_MAP.items():
            count = self.db.query(Match).filter(
                Match.odds_source == old
            ).count()
            if count > 0:
                if not dry_run:
                    self.db.query(Match).filter(
                        Match.odds_source == old
                    ).update({"odds_source": new})
                fixed += count
        if not dry_run and fixed > 0:
            self.db.flush()
        logger.info(f"[cleaner:source] {'[DRY-RUN] ' if dry_run else ''}Fixed {fixed} odds_source mappings")
        return fixed

    def _fix_enum_consistency(self, dry_run: bool) -> int:
        """修复 raw string → enum value。"""
        fixed = 0
        status_map = {
            "finished": "finished",
            "scheduled": "scheduled",
            "upcoming": "upcoming",
            "live": "live",
            "postponed": "postponed",
        }
        type_map = {
            "FRIENDLY": "friendly",
            "WORLD_CUP": "world_cup",
            "WARM_UP": "warm_up",
            "QUALIFIER": "qualifier",
        }

        for raw, norm in type_map.items():
            count = self.db.query(Match).filter(
                Match.match_type == raw
            ).count()
            if count > 0:
                if not dry_run:
                    self.db.query(Match).filter(
                        Match.match_type == raw
                    ).update({"match_type": norm})
                fixed += count

        for raw, norm in status_map.items():
            count = self.db.query(Match).filter(
                Match.status == raw
            ).count()
            if count > 0:
                if not dry_run:
                    self.db.query(Match).filter(
                        Match.status == raw
                    ).update({"status": norm})
                fixed += count

        if not dry_run and fixed > 0:
            self.db.flush()
        logger.info(f"[cleaner:enum] {'[DRY-RUN] ' if dry_run else ''}Fixed {fixed} enum values")
        return fixed
