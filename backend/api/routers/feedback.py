from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel

from database.models import get_db, Feedback, FeedbackLike, User
from schemas import FeedbackListResponse, FeedbackCreateResponse, FeedbackLikeResponse
from auth import get_optional_user, get_current_active_user

router = APIRouter(prefix="/api/feedback", tags=["feedback"])

class FeedbackCreate(BaseModel):
    category: str
    content: str
    match_id: Optional[int] = None
    is_anonymous: bool = False

@router.get("", response_model=FeedbackListResponse)
def list_feedback(
    category: Optional[str] = None,
    match_id: Optional[int] = None,
    limit: int = 50,
    offset: int = 0,
    db: Session = Depends(get_db),
):
    q = db.query(Feedback).order_by(Feedback.created_at.desc())
    if category:
        q = q.filter(Feedback.category == category)
    if match_id:
        q = q.filter(Feedback.match_id == match_id)
    total = q.count()
    items = q.offset(offset).limit(min(limit, 100)).all()

    # Batch-fetch users to avoid N+1
    user_ids = {fb.user_id for fb in items if fb.user_id and not fb.is_anonymous}
    user_map = {}
    if user_ids:
        users = db.query(User).filter(User.id.in_(user_ids)).all()
        user_map = {u.id: u for u in users}

    results = []
    for fb in items:
        author = "匿名用户"
        if fb.user_id and not fb.is_anonymous:
            u = user_map.get(fb.user_id)
            if u:
                author = u.email.split("@")[0]
        results.append({
            "id": fb.id,
            "category": fb.category,
            "match_id": fb.match_id,
            "content": fb.content,
            "is_anonymous": fb.is_anonymous,
            "likes": fb.likes,
            "author": author,
            "created_at": fb.created_at.isoformat() if fb.created_at else None,
        })
    return {"items": results, "total": total}

@router.post("", response_model=FeedbackCreateResponse)
def create_feedback(
    data: FeedbackCreate,
    user: User = Depends(get_optional_user),
    db: Session = Depends(get_db),
):
    if len(data.content.strip()) < 5:
        raise HTTPException(400, "内容至少5个字符")
    if len(data.content) > 2000:
        raise HTTPException(400, "内容不超过2000字符")
    if data.category not in ("suggestion", "bug", "data_issue", "discussion"):
        raise HTTPException(400, "无效分类")

    fb = Feedback(
        user_id=user.id if user else None,
        category=data.category,
        match_id=data.match_id,
        content=data.content.strip(),
        is_anonymous=data.is_anonymous,
    )
    db.add(fb)
    db.commit()
    db.refresh(fb)
    return {"id": fb.id, "status": "created"}

@router.post("/{feedback_id}/like", response_model=FeedbackLikeResponse)
def like_feedback(
    feedback_id: int,
    user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
):
    fb = db.query(Feedback).filter(Feedback.id == feedback_id).first()
    if not fb:
        raise HTTPException(404, "留言不存在")
    existing = db.query(FeedbackLike).filter(
        FeedbackLike.feedback_id == feedback_id,
        FeedbackLike.user_id == user.id,
    ).first()
    if existing:
        db.delete(existing)
        fb.likes = max(0, (fb.likes or 1) - 1)
        db.commit()
        return {"id": feedback_id, "likes": fb.likes, "action": "unliked"}
    like = FeedbackLike(feedback_id=feedback_id, user_id=user.id)
    db.add(like)
    fb.likes = (fb.likes or 0) + 1
    db.commit()
    return {"id": feedback_id, "likes": fb.likes, "action": "liked"}
