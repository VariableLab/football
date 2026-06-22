"""
置信度评估层 — 基于信息熵与市场共识

第一性原理：
1. 熵越高，不确定性越大
2. 与市场分歧越大，风险越高
"""
from __future__ import annotations

from typing import Dict, Optional
import numpy as np


def compute_confidence(
    spf: Dict[str, float],
    market: Optional[Dict[str, float]],
    ctx,
) -> str:
    """
    基于信息熵与市场共识的置信度评估。
    
    Returns: "high" | "medium" | "low"
    """
    probs = np.array([spf["home"], spf["draw"], spf["away"]])
    entropy = -np.sum(probs * np.log(probs + 1e-8))
    norm_entropy = entropy / 1.098  # ln(3)

    agreement = 1.0
    if market:
        m_probs = np.array([market.get("home", 0.33), market.get("draw", 0.33), market.get("away", 0.33)])
        agreement = np.dot(probs, m_probs) / (np.linalg.norm(probs) * np.linalg.norm(m_probs))

    if norm_entropy < 0.4 and agreement > 0.95:
        return "high"
    if norm_entropy < 0.7 and agreement > 0.85:
        return "medium"
    return "low"
