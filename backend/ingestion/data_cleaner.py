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
    from ingestion.data_cleaner import DataCleaner
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
from typing import Dict, List, Optional

from sqlalchemy import text
from database.models import (
    Match, MatchStatus, MatchType,
    Team, OddsHistory, Prediction,
)

import yaml
import os

logger = logging.getLogger(__name__)

# ─── 受控词表 ───
VALID_MATCH_CODE_PREFIXES = frozenset({"WC2026", "FR", "JC", "FRIENDLY", "QUAL"})
VALID_ODDS_SOURCES = frozenset({
    "sporttery", "football-data-B365", "football-data-PS", 
    "football-data-PH", "odds-api", "internal"
})
VALID_PLAY_TYPES = frozenset({"SPF", "RQ", "SCORE", "GOALS", "HALF"})

# ─── 队名别名映射 (支持 YAML 动态加载) ───
_TEAM_CFG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "team_aliases.yaml")

def load_team_aliases() -> Dict[str, str]:
    """从 YAML 加载队名映射，失败时返回空字典"""
    if os.path.exists(_TEAM_CFG_PATH):
        try:
            with open(_TEAM_CFG_PATH, "r", encoding="utf-8") as f:
                return yaml.safe_load(f) or {}
        except Exception as e:
            logger.warning(f"[config] Failed to load team_aliases.yaml: {e}")
    return {}

TEAM_ALIASES = load_team_aliases()


def resolve_team_name(raw: str) -> str:
    """将原始队名解析为规范名。"""
    if not raw:
        return raw
    stripped = raw.strip()
    # 优先查动态加载的别名库
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
    # 💡 只有当 raw_name 恰好是 3 字符时，才允许通过 Team.code 精确对齐，防止 Brescia 撞上 BRE 国家队代码
    if len(raw_name) == 3:
        team = db.query(Team).filter(Team.code == raw_name.upper()).first()
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

        # 6. 💡 核心增加：重复球队自动合并
        teams_merged = self._fix_team_names(dry_run)
        result.fixed["teams_merged"] = teams_merged

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
        # PostgreSQL 兼容性修复
        # 检查 recorded_at 是否没有时区偏移（在 PG 中这通常意味着数据同步时丢失了偏移量）
        # 我们这里简化处理，主要关注审计 findings
        return findings

    def _audit_odds_duplicates(self) -> List[AuditFinding]:
        findings = []
        # Multi-DB 兼容性修复 (SQLite vs PostgreSQL): 5分钟 slot 的计算 (1天=86400秒)
        dialect = self.db.bind.dialect.name
        if dialect == "sqlite":
            dup_count_query = text("""
                SELECT COUNT(*) FROM (
                    SELECT match_id, source,
                           (cast(strftime('%s', recorded_at) as integer) / 300) as slot,
                           COUNT(*) as cnt
                    FROM odds_history
                    GROUP BY match_id, source, slot
                    HAVING COUNT(*) > 1
                ) sub
            """)
        else:
            dup_count_query = text("""
                SELECT COUNT(*) FROM (
                    SELECT match_id, source,
                           (extract(epoch from recorded_at)::bigint / 300) as slot,
                           COUNT(*) as cnt
                    FROM odds_history
                    GROUP BY match_id, source, slot
                    HAVING COUNT(*) > 1
                ) sub
            """)
        try:
            dup_count = self.db.execute(dup_count_query).scalar() or 0
            if dup_count > 0:
                findings.append(AuditFinding(
                    category="odds_dedup", severity="warning",
                    table="odds_history", count=dup_count,
                    description=f"{dup_count} 组赔率重复 (同 match_id/source/5min)",
                    fixable=True,
                ))
        except Exception as e:
            logger.warning(f"Audit duplicates failed: {e}")
            
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
        # Multi-DB 兼容性修复 (SQLite vs PostgreSQL)
        dialect = self.db.bind.dialect.name
        if dialect == "sqlite":
            dup_ids_query = text("""
                SELECT id FROM (
                    SELECT id,
                           ROW_NUMBER() OVER (
                               PARTITION BY match_id, source, (cast(strftime('%s', recorded_at) as integer) / 300)
                               ORDER BY recorded_at DESC, id DESC
                           ) as rn
                    FROM odds_history
                ) sub
                WHERE rn > 1
            """)
        else:
            dup_ids_query = text("""
                SELECT id FROM (
                    SELECT id,
                           ROW_NUMBER() OVER (
                               PARTITION BY match_id, source, (extract(epoch from recorded_at)::bigint / 300)
                               ORDER BY recorded_at DESC, id DESC
                           ) as rn
                    FROM odds_history
                ) sub
                WHERE rn > 1
            """)
        try:
            dup_ids = self.db.execute(dup_ids_query).fetchall()
            delete_ids = [row[0] for row in dup_ids]
            count = len(delete_ids)
            if count > 0 and not dry_run:
                batch_size = 500
                for i in range(0, count, batch_size):
                    batch = delete_ids[i:i + batch_size]
                    self.db.query(OddsHistory).filter(
                        OddsHistory.id.in_(batch)
                    ).delete(synchronize_session=False)
                self.db.flush()
            return count
        except Exception as e:
            logger.warning(f"Fix duplicates failed: {e}")
            return 0

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

    def _fix_team_names(self, dry_run: bool) -> int:
        """
        根据别名映射合并重复球队记录。
        """
        fixed = 0
        for alias, canonical in TEAM_ALIASES.items():
            # 查找规范球队
            primary = self.db.query(Team).filter(Team.name == canonical).first()
            # 查找别名记录
            dupes = self.db.query(Team).filter(Team.name == alias).all()
            
            if primary and dupes:
                for dupe in dupes:
                    if dupe.id == primary.id:
                        continue
                    
                    if not dry_run:
                        # 1. 迁移比赛
                        self.db.query(Match).filter(Match.home_team_id == dupe.id).update({Match.home_team_id: primary.id})
                        self.db.query(Match).filter(Match.away_team_id == dupe.id).update({Match.away_team_id: primary.id})
                        
                        # 2. 迁移球员数据 (PlayerStats) - 防止外键约束报错崩溃
                        from database.models import PlayerStats
                        self.db.query(PlayerStats).filter(PlayerStats.team_id == dupe.id).update({PlayerStats.team_id: primary.id})
                        
                        # 3. 删除重复记录
                        self.db.delete(dupe)
                        
                    fixed += 1
                    
        if not dry_run and fixed > 0:
            self.db.flush()
        logger.info(f"[cleaner:teams] {'[DRY-RUN] ' if dry_run else ''}Merged {fixed} duplicate team records")
        return fixed
