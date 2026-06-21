"""Auto-Learner: 结果同步→验证→NN增量训练→自愈闭环"""
import glob
from datetime import datetime, timezone, timedelta
from database.models import SessionLocal, Match, MatchStatus
from utils.logger import get_logger
logger = get_logger("auto_learner")
MIN_NEW = 5

def auto_learn_trigger():
    s = SessionLocal()
    try:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=1)
        new = s.query(Match).filter(Match.status==MatchStatus.FINISHED, Match.actual_outcome.isnot(None), Match.updated_at>=cutoff).count()
    finally: s.close()
    if new < MIN_NEW: return {"triggered":False,"new_results":new}
    logger.info(f"[auto-learn] {new} new results, triggering incremental training")
    result = {"triggered":True,"new_results":new,"trained":[]}
    
    # Train draw classifier
    try:
        from core.draw_classifier import draw_classifier_train_job
        draw_classifier_train_job()
        result["trained"].append("draw_classifier")
    except Exception as e:
        logger.warning(f"[auto-learn] draw_classifier: {e}")

    # Train residual NN
    try:
        lrfs = glob.glob("./data/weights/lr/global_*.json")
        if lrfs: 
            from core.residual_nn import StackingTrainer
            s_local = SessionLocal()
            trainer = StackingTrainer(db_session=s_local)
            trainer.train()
            s_local.close()
            result["trained"].append("residual_nn")
    except Exception as e: logger.warning(f"[auto-learn] residual_nn: {e}")
    return result

def auto_verify_jingcai():
    from database.models import SessionLocal, JingcaiIssue, JingcaiIssueMatch
    from jingcai_predictor import record_draw_result, verify_issue
    s = SessionLocal()
    try:
        drawn = s.query(JingcaiIssue).filter(JingcaiIssue.status=='drawn', JingcaiIssue.verification==None).all()
        verified = 0
        for iss in drawn:
            ims = s.query(JingcaiIssueMatch).filter(JingcaiIssueMatch.issue_id==iss.id).order_by(JingcaiIssueMatch.sequence).all()
            results = []
            for im in ims:
                m = im.match
                if m and m.actual_outcome in ("home","draw","away"): results.append(m.actual_outcome)
                else: break
            if len(results)!=len(ims) or not results: continue
            try:
                record_draw_result(s, iss.issue_id, results)
                v = verify_issue(s, iss.issue_id)
                s.commit(); verified += 1
                logger.info(f"[auto-learn] Verified {iss.issue_id}: {v.get('spf_hits',0)}/{v.get('total',len(results))}")
            except Exception as e:
                logger.warning(f"[auto-learn] Verify {iss.issue_id}: {e}"); s.rollback()
    finally: s.close()
