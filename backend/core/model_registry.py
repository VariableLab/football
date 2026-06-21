"""
模型注册表 — 统一管理所有预测模型版本

功能:
- 注册新模型版本
- 标记活跃版本
- 版本回滚
- 记录准确率/Brier Score
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, Optional, List
from sqlalchemy import Column, Integer, String, Float, DateTime, Text, Boolean, JSON
from sqlalchemy.orm import Session


@dataclass
class ModelVersion:
    """模型版本元数据"""
    version: str
    name: str
    description: str
    is_active: bool
    deployed_at: Optional[datetime]
    accuracy: Optional[float]
    brier_score: Optional[float]
    sample_count: int
    feature_dim: int
    metrics: Dict[str, float] = field(default_factory=dict)


class ModelRegistry:
    """模型注册表 — 统一管理所有模型版本"""

    def __init__(self, db_session: Session):
        self.db = db_session

    def register(
        self,
        version: str,
        name: str,
        description: str,
        accuracy: Optional[float] = None,
        brier_score: Optional[float] = None,
        sample_count: int = 0,
        feature_dim: int = 48,
        metrics: Optional[Dict[str, float]] = None,
    ) -> ModelVersion:
        """注册新模型版本"""
        from database.models import ModelVersion as ModelVersionTable

        # 检查是否已存在
        existing = self.db.query(ModelVersionTable).filter(
            ModelVersionTable.version == version
        ).first()

        if existing:
            # 更新已有记录
            existing.name = name
            existing.description = description
            existing.accuracy = accuracy
            existing.brier_score = brier_score
            existing.sample_count = sample_count
            existing.feature_dim = feature_dim
            existing.metrics = json.dumps(metrics or {})
            existing.deployed_at = datetime.now(timezone.utc)
            self.db.commit()
        else:
            # 新建记录
            entry = ModelVersionTable(
                version=version,
                name=name,
                description=description,
                accuracy=accuracy,
                brier_score=brier_score,
                sample_count=sample_count,
                feature_dim=feature_dim,
                metrics=json.dumps(metrics or {}),
                deployed_at=datetime.now(timezone.utc),
            )
            self.db.add(entry)
            self.db.commit()

        return ModelVersion(
            version=version,
            name=name,
            description=description,
            is_active=(version == self.get_active_version()),
            deployed_at=datetime.now(timezone.utc),
            accuracy=accuracy,
            brier_score=brier_score,
            sample_count=sample_count,
            feature_dim=feature_dim,
            metrics=metrics or {},
        )

    def get_active_version(self) -> str:
        """获取当前活跃版本"""
        from database.models import ModelVersion as ModelVersionTable
        entry = self.db.query(ModelVersionTable).filter(
            ModelVersionTable.is_active == True
        ).order_by(ModelVersionTable.deployed_at.desc()).first()
        return entry.version if entry else ""

    def activate_version(self, version: str) -> bool:
        """激活指定版本(停用其他版本)"""
        from database.models import ModelVersion as ModelVersionTable

        # 停用所有
        self.db.query(ModelVersionTable).update(
            {ModelVersionTable.is_active: False}
        )

        # 激活目标
        result = self.db.query(ModelVersionTable).filter(
            ModelVersionTable.version == version
        ).update({ModelVersionTable.is_active: True})

        self.db.commit()
        return result > 0

    def rollback(self, version: str) -> bool:
        """回滚到指定版本"""
        return self.activate_version(version)

    def list_versions(self) -> List[ModelVersion]:
        """列出所有模型版本"""
        from database.models import ModelVersion as ModelVersionTable
        entries = self.db.query(ModelVersionTable).order_by(
            ModelVersionTable.deployed_at.desc()
        ).all()

        versions = []
        for e in entries:
            metrics = {}
            if e.metrics:
                try:
                    metrics = json.loads(e.metrics)
                except (json.JSONDecodeError, TypeError):
                    pass
            versions.append(ModelVersion(
                version=e.version,
                name=e.name,
                description=e.description or "",
                is_active=bool(e.is_active),
                deployed_at=e.deployed_at,
                accuracy=e.accuracy,
                brier_score=e.brier_score,
                sample_count=e.sample_count,
                feature_dim=e.feature_dim or 48,
                metrics=metrics,
            ))
        return versions

    def get_version(self, version: str) -> Optional[ModelVersion]:
        """获取指定版本"""
        from database.models import ModelVersion as ModelVersionTable
        entry = self.db.query(ModelVersionTable).filter(
            ModelVersionTable.version == version
        ).first()
        if not entry:
            return None

        metrics = {}
        if entry.metrics:
            try:
                metrics = json.loads(entry.metrics)
            except (json.JSONDecodeError, TypeError):
                pass

        return ModelVersion(
            version=entry.version,
            name=entry.name,
            description=entry.description or "",
            is_active=bool(entry.is_active),
            deployed_at=entry.deployed_at,
            accuracy=entry.accuracy,
            brier_score=entry.brier_score,
            sample_count=entry.sample_count,
            feature_dim=entry.feature_dim or 48,
            metrics=metrics,
        )
