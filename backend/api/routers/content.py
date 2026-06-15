import sys
import os
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import Optional

# 将 research/src 加入路径以引用内容引擎
_current_dir = Path(__file__).resolve().parent
_root_dir = _current_dir.parent.parent.parent
_research_src = _root_dir / "research" / "src"
if str(_research_src) not in sys.path:
    sys.path.append(str(_research_src))

from database.models import get_db, Match, MatchStatus
from footy.content.engine import WorldCupContentEngine
from api.auth import get_optional_user
import os
from datetime import datetime

router = APIRouter(prefix="/api/content", tags=["Content"])

@router.get("/preview/{match_id}")
def get_match_preview(
    match_id: int, 
    db: Session = Depends(get_db),
    user: Optional[any] = Depends(get_optional_user)
):
    """
    获取单场比赛的 AI 深度前瞻内容。
    数据来源于 research 层的 WorldCupContentEngine。
    对未完赛的比赛实施商业化数据截断。
    """
    try:
        # 验证是否为结束比赛
        match = db.query(Match).filter(Match.id == match_id).first()
        if not match:
            raise HTTPException(status_code=404, detail="Match not found")
            
        is_finished = match.status == MatchStatus.FINISHED
        TEST_MODE = os.getenv("TEST_MODE", "false").lower() == "true"
        
        # 验证付费权限
        has_access = is_finished or TEST_MODE
        if not has_access and user and user.is_paid:
            if user.paid_until:
                from datetime import timezone as _tz
                paid_until = user.paid_until.replace(tzinfo=_tz.utc) if user.paid_until.tzinfo is None else user.paid_until
                if paid_until >= datetime.now(_tz.utc):
                    has_access = True
                else:
                    user.is_paid = False
                    db.commit()

        engine = WorldCupContentEngine(db)
        preview = engine.generate_match_preview(match_id)
        if not preview:
            raise HTTPException(status_code=404, detail="Preview not found for this match")
            
        # 商业化截断逻辑
        if not has_access:
            preview['content']['insight'] = preview['content']['insight'] + "\n\n🔒 [Pro 专享] 订阅以解锁基于 59 维特征向量的完整模型研判与精确概率。"
            preview['quant']['probabilities'] = {
                "home": "🔒",
                "draw": "🔒",
                "away": "🔒"
            }
            preview['quant']['confidence'] = "🔒"
            preview['content']['headline'] = "🔒 锁定: " + preview['content']['headline'][:4] + "..."
            
            # 清空球星深度数据，仅展示名字
            for side in ['home', 'away']:
                if 'stars' in preview['teams'][side]:
                    for star in preview['teams'][side]['stars']:
                        star['xg'] = "🔒"
                        star['goals'] = "🔒"
            
            preview['is_pro_locked'] = True
        else:
            preview['is_pro_locked'] = False

        return preview
    except HTTPException:
        raise
    except Exception as e:
        # 捕获可能的导入或配置错误
        raise HTTPException(status_code=500, detail=f"Content Engine Error: {str(e)}")
