"""录入竞彩开奖结果 v2 — 修复 unknown 值"""
from models import SessionLocal, JingcaiIssue, JingcaiIssueMatch, Match
from jingcai_predictor import record_draw_result

VALID_OUTCOMES = {"home", "draw", "away"}

s = SessionLocal()
for iss_id in ["JC20260508","JC20260509","JC20260510","JC20260511",
                "JC20260512","JC20260513","JC20260514"]:
    iss = s.query(JingcaiIssue).filter(JingcaiIssue.issue_id==iss_id).first()
    if not iss or iss.draw_result is not None:
        continue
    
    ims = s.query(JingcaiIssueMatch).filter(
        JingcaiIssueMatch.issue_id==iss.id
    ).order_by(JingcaiIssueMatch.sequence).all()
    
    results = []
    missing = 0
    for im in ims:
        m = im.match
        outcome = m.actual_outcome if m else None
        if outcome in VALID_OUTCOMES:
            results.append(outcome)
        elif outcome and outcome != "unknown":
            results.append(outcome)
        else:
            missing += 1
    
    if missing > 0:
        print(f"  {iss_id}: {len(ims)}场, {missing}场无有效结果, 跳过")
        continue
    
    record_draw_result(s, iss_id, results)
    s.commit()
    print(f"  {iss_id}: OK 录入 {len(results)} 场")

s.close()
