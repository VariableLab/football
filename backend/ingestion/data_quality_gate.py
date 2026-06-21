"""
数据质量门禁 — 写入数据库前校验

在数据采集和预测写入时自动触发,拦截无效数据。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional, List
from datetime import datetime, timezone


@dataclass
class QualityFinding:
    category: str
    severity: str  # critical / warning / info
    description: str
    match_id: Optional[int] = None


class DataQualityGate:
    """数据质量门禁 — 每次写入DB前检查"""

    @staticmethod
    def check_odds(
        match_id: int,
        odds_home: Optional[float],
        odds_draw: Optional[float],
        odds_away: Optional[float],
    ) -> List[QualityFinding]:
        """赔率合理性检查"""
        findings = []

        if odds_home is None or odds_draw is None or odds_away is None:
            findings.append(QualityFinding(
                category="odds", severity="warning",
                description="赔率不完整(有None值)", match_id=match_id
            ))
            return findings

        # 1. 赔率必须 > 1.01
        for sel, o in [("home", odds_home), ("draw", odds_draw), ("away", odds_away)]:
            if o <= 1.01:
                findings.append(QualityFinding(
                    category="odds", severity="critical",
                    description=f"赔率过低: {sel}={o:.2f} (<=1.01)", match_id=match_id
                ))

        # 2. 隐含概率总和应在 1.05-1.20 之间(正常返水率)
        implied = sum(1.0 / o for o in [odds_home, odds_draw, odds_away])
        if implied < 1.03:
            findings.append(QualityFinding(
                category="odds", severity="critical",
                description=f"返水率异常低: implied={implied:.3f} (<1.03)", match_id=match_id
            ))
        elif implied > 1.25:
            findings.append(QualityFinding(
                category="odds", severity="warning",
                description=f"返水率异常高: implied={implied:.3f} (>1.25)", match_id=match_id
            ))

        # 3. 赔率不应完全相等(除非是极弱的比赛)
        if abs(odds_home - odds_draw) < 0.01 and abs(odds_draw - odds_away) < 0.01:
            findings.append(QualityFinding(
                category="odds", severity="info",
                description="三赔率完全相等,可能是合成数据", match_id=match_id
            ))

        return findings

    @staticmethod
    def check_prediction(match_id: int, probabilities: Dict[str, float], play_type: str) -> List[QualityFinding]:
        """预测完整性检查"""
        findings = []

        if not probabilities:
            findings.append(QualityFinding(
                category="prediction", severity="critical",
                description="预测概率为空", match_id=match_id,
            ))
            return findings

        total = sum(probabilities.values())
        if abs(total - 1.0) > 0.02:
            findings.append(QualityFinding(
                category="prediction", severity="critical",
                description=f"概率和不等于1.0: {total:.4f}", match_id=match_id,
            ))

        # 检查是否全是默认值
        if play_type == "SPF":
            vals = list(probabilities.values())
            if all(abs(v - 0.333) < 0.01 for v in vals):
                findings.append(QualityFinding(
                    category="prediction", severity="warning",
                    description="预测概率全为均匀分布(0.333),可能是回退值", match_id=match_id,
                ))

        # 检查是否有负值
        for k, v in probabilities.items():
            if v < 0:
                findings.append(QualityFinding(
                    category="prediction", severity="critical",
                    description=f"负概率: {k}={v}", match_id=match_id,
                ))

        return findings

    @staticmethod
    def check_team_metadata(team_id: int, elo: Optional[int], fifa_rank: Optional[int], name: str) -> List[QualityFinding]:
        """球队元数据完整性检查"""
        findings = []

        if elo is None or elo == 1500:
            findings.append(QualityFinding(
                category="team", severity="warning",
                description=f"球队 {name}(id={team_id}) Elo为默认值1500或未设置",
            ))

        if fifa_rank is None:
            findings.append(QualityFinding(
                category="team", severity="info",
                description=f"球队 {name}(id={team_id}) FIFA排名缺失",
            ))

        return findings

    @staticmethod
    def audit_all(odds_findings: List, pred_findings: List, team_findings: List) -> Dict[str, int]:
        """汇总审计结果"""
        counts = {"critical": 0, "warning": 0, "info": 0}
        for findings in [odds_findings, pred_findings, team_findings]:
            for f in findings:
                counts[f.severity] = counts.get(f.severity, 0) + 1
        return counts
