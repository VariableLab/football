"""
预测快照生成与锁定

功能:
- 在赛前生成预测快照并锁定
- 快照包含完整预测结果、模型版本、时间戳
- SHA-256校验和确保数据完整性
- 快照一旦锁定不可修改

用法:
  python3 prediction_snapshot.py --match-id 123    # 为单场比赛生成快照
  python3 prediction_snapshot.py --upcoming --hours 1  # 为1小时内比赛生成快照
  python3 prediction_snapshot.py --verify 123       # 验证快照完整性
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy.orm import Session

from database.models import SessionLocal, Match, MatchStatus, PredictionSnapshot
from core.prediction_engine import PredictionEngine, build_context_from_match
from utils.logger import get_logger

logger = get_logger("prediction_snapshot")


class PredictionSnapshotManager:
    """预测快照管理器"""

    def __init__(self, db: Session):
        self.db = db
        self.engine = PredictionEngine(db_session=db)

    def generate_snapshot(self, match_id: int) -> Optional[PredictionSnapshot]:
        """为指定比赛生成预测快照"""
        match = self.db.query(Match).get(match_id)
        if not match:
            logger.error(f"[snapshot] Match {match_id} not found")
            return None

        # 检查是否已有快照
        existing = self.db.query(PredictionSnapshot).filter(
            PredictionSnapshot.match_id == match_id,
            PredictionSnapshot.is_locked == True
        ).first()
        if existing:
            logger.info(f"[snapshot] Snapshot already exists for match {match_id}")
            return existing

        # 构建上下文并生成预测
        try:
            ctx = build_context_from_match(match)
            result = self.engine.predict(ctx)
        except Exception as e:
            logger.error(f"[snapshot] Prediction failed for match {match_id}: {e}")
            return None

        # 构建快照JSON
        snapshot_data = {
            "match_id": match_id,
            "match_code": match.match_code,
            "home_team_id": match.home_team_id,
            "away_team_id": match.away_team_id,
            "kickoff_at": match.kickoff_at.isoformat() if match.kickoff_at else None,
            "competition": match.competition,
            "stage": match.stage,
            "prediction": {
                "spf": result.spf,
                "rq": result.rq,
                "score": result.score,
                "goals": result.goals,
                "half": result.half,
            },
            "model_version": result.model_version,
            "confidence": result.confidence,
            "weights_used": result.weights_used,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }

        snapshot_json = json.dumps(snapshot_data, ensure_ascii=False, sort_keys=True)
        checksum = hashlib.sha256(snapshot_json.encode("utf-8")).hexdigest()

        # 创建快照记录
        snapshot = PredictionSnapshot(
            match_id=match_id,
            snapshot_json=snapshot_json,
            checksum=checksum,
            model_version=result.model_version,
            locked_at=datetime.now(timezone.utc),
            is_locked=True,
        )

        self.db.add(snapshot)
        self.db.commit()

        logger.info(f"[snapshot] Created snapshot for match {match_id}: checksum={checksum[:12]}...")
        return snapshot

    def generate_for_upcoming(self, hours: int = 1) -> int:
        """为未来N小时内的比赛生成快照"""
        now = datetime.now(timezone.utc)
        cutoff = now + timedelta(hours=hours)

        matches = self.db.query(Match).filter(
            Match.status.in_([MatchStatus.SCHEDULED, MatchStatus.UPCOMING]),
            Match.kickoff_at.isnot(None),
            Match.kickoff_at <= cutoff,
            Match.kickoff_at >= now,
        ).all()

        logger.info(f"[snapshot] Found {len(matches)} upcoming matches within {hours}h")

        created = 0
        for match in matches:
            snapshot = self.generate_snapshot(match.id)
            if snapshot:
                created += 1

        return created

    def verify_snapshot(self, match_id: int) -> bool:
        """验证快照完整性"""
        snapshot = self.db.query(PredictionSnapshot).filter(
            PredictionSnapshot.match_id == match_id
        ).first()

        if not snapshot:
            logger.error(f"[snapshot] No snapshot found for match {match_id}")
            return False

        # 重新计算校验和
        expected_checksum = hashlib.sha256(snapshot.snapshot_json.encode("utf-8")).hexdigest()
        
        if expected_checksum == snapshot.checksum:
            logger.info(f"[snapshot] Snapshot verified for match {match_id}")
            return True
        else:
            logger.error(f"[snapshot] Checksum mismatch for match {match_id}: expected={expected_checksum[:12]}... got={snapshot.checksum[:12]}...")
            return False

    def get_snapshot(self, match_id: int) -> Optional[dict]:
        """获取快照数据"""
        snapshot = self.db.query(PredictionSnapshot).filter(
            PredictionSnapshot.match_id == match_id
        ).first()

        if not snapshot:
            return None

        return json.loads(snapshot.snapshot_json)


# ────────────────────────────
# CLI
# ────────────────────────────
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Prediction snapshot manager")
    parser.add_argument("--match-id", type=int, help="Generate snapshot for specific match")
    parser.add_argument("--upcoming", action="store_true", help="Generate snapshots for upcoming matches")
    parser.add_argument("--hours", type=int, default=1, help="Hours ahead for upcoming matches")
    parser.add_argument("--verify", type=int, help="Verify snapshot integrity for match")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        manager = PredictionSnapshotManager(db)

        if args.verify:
            ok = manager.verify_snapshot(args.verify)
            print(f"Verification: {'PASS' if ok else 'FAIL'}")
        elif args.match_id:
            snapshot = manager.generate_snapshot(args.match_id)
            if snapshot:
                print(f"Snapshot created: checksum={snapshot.checksum[:16]}...")
            else:
                print("Failed to create snapshot")
        elif args.upcoming:
            count = manager.generate_for_upcoming(hours=args.hours)
            print(f"Created {count} snapshots for upcoming matches")
        else:
            parser.print_help()
    finally:
        db.close()
