import sys
import os
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

# 将 research/src 加入路径以引用内容引擎
_current_dir = Path(__file__).resolve().parent
_root_dir = _current_dir.parent.parent.parent
_research_src = _root_dir / "research" / "src"
if str(_research_src) not in sys.path:
    sys.path.append(str(_research_src))

from database.models import get_db
from footy.content.engine import WorldCupContentEngine

router = APIRouter(prefix="/api/content", tags=["Content"])

@router.get("/preview/{match_id}")
def get_match_preview(match_id: int, db: Session = Depends(get_db)):
    """
    获取单场比赛的 AI 深度前瞻内容。
    数据来源于 research 层的 WorldCupContentEngine。
    """
    try:
        engine = WorldCupContentEngine(db)
        preview = engine.generate_match_preview(match_id)
        if not preview:
            raise HTTPException(status_code=404, detail="Preview not found for this match")
        return preview
    except Exception as e:
        # 捕获可能的导入或配置错误
        raise HTTPException(status_code=500, detail=f"Content Engine Error: {str(e)}")
