"""
影子系统与多级别模型对比审计脚本 — shadow_audit.py

目的：
1. 对比生产环境 v2.0、一致性混合 v3.0、以及深度学习时序 xG v4.0 模型在预测表现上的差异。
2. 使用经典概率评估指标：RPS（Ranked Probability Score）、Brier Score、方向准确率（Accuracy）。
3. 评估模拟盘口收益差异 (ROI)。
"""

import sys
import os
import json
import numpy as np
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Tuple, Optional

# 确保 backend 及其子包在 sys.path 中
_current_dir = os.path.dirname(os.path.abspath(__file__))
_backend_root = os.path.dirname(_current_dir)
for d in ["api", "core", "features", "ingestion", "database", "strategy", "monitor", "utils", "scripts"]:
    _path = os.path.join(_backend_root, d)
    if _path not in sys.path:
        sys.path.append(_path)
if _backend_root not in sys.path:
    sys.path.append(_backend_root)

from database.models import SessionLocal, Match, MatchStatus, Prediction, PlayType
from prediction_engine import PredictionEngine, build_context_from_match
from utils.logger import get_logger

logger = get_logger("shadow_audit")

# 评估指标辅助函数
def calculate_brier_score(probs: Dict[str, float], actual: str) -> float:
    brier = 0.0
    for outcome in ["home", "draw", "away"]:
        p = probs.get(outcome, 0.0)
        o = 1.0 if outcome == actual else 0.0
        brier += (p - o) ** 2
    return brier

def calculate_rps(probs: Dict[str, float], actual: str) -> float:
    p_h = probs.get("home", 0.0)
    p_d = probs.get("draw", 0.0)
    p_a = probs.get("away", 0.0)
    
    o_h = 1.0 if actual == "home" else 0.0
    o_d = 1.0 if actual == "draw" else 0.0
    o_a = 1.0 if actual == "away" else 0.0
    
    cum_p1 = p_h
    cum_o1 = o_h
    cum_p2 = p_h + p_d
    cum_o2 = o_h + o_d
    
    return 0.5 * ((cum_p1 - cum_o1) ** 2 + (cum_p2 - cum_o2) ** 2)

def ensure_shadow_predictions(db, limit=200) -> int:
    """确保最近的 finished 比赛具有所有模型的预测，没有则补算"""
    logger.info(f"Checking and supplementing predictions for the last {limit} finished matches...")
    matches = db.query(Match).filter(
        Match.status == MatchStatus.FINISHED,
        Match.actual_outcome.isnot(None)
    ).order_by(Match.kickoff_at.desc()).all()
    
    valid_matches = []
    for match in matches:
        if match.home_team is None or match.away_team is None:
            continue
        valid_matches.append(match)
        if len(valid_matches) >= limit:
            break
            
    recalc_count = 0
    engine = PredictionEngine(db_session=db)
    
    for match in valid_matches:
        # 检查是否同时具有 v2.0, v3.0, v4.0 的 SPF 预测
        preds = db.query(Prediction).filter(
            Prediction.match_id == match.id,
            Prediction.play_type == PlayType.SPF
        ).all()
        
        versions = {p.model_version for p in preds}
        
        # 如果缺少任意一个主版本，就重新生成该场的所有预测并更新入库
        if "v2.0" not in versions or "v3.0" not in versions or "v4.0" not in versions:
            db.query(Prediction).filter(
                Prediction.match_id == match.id,
                Prediction.model_version.in_(["v2.0", "v3.0", "v3.0_shadow", "v3.0_classic", "v4.0"])
            ).delete()
            
            ctx = build_context_from_match(match)
            if ctx is None:
                continue
            
            try:
                res = engine.predict(ctx)
                from scripts.regenerate_predictions import _compute_checksum
                checksum = _compute_checksum(ctx)
                
                for p in res.to_db_payload():
                    db.add(Prediction(
                        match_id=match.id,
                        play_type=p["play_type"],
                        probabilities=p["probabilities"],
                        input_checksum=checksum,
                        model_version=p["model_version"]
                    ))
                recalc_count += 1
            except Exception as e:
                logger.error(f"Failed to generate prediction for match {match.id}: {e}")
                continue
            
    if recalc_count > 0:
        db.commit()
        logger.info(f"Supplemented predictions for {recalc_count} matches.")
    else:
        logger.info("All selected finished matches already have complete predictions.")
    return recalc_count

def run_audit(limit=200):
    db = SessionLocal()
    try:
        # 1. 补全影子预测
        ensure_shadow_predictions(db, limit)
        
        # 2. 拉取我们要审计的已结束比赛
        raw_matches = db.query(Match).filter(
            Match.status == MatchStatus.FINISHED,
            Match.actual_outcome.isnot(None)
        ).order_by(Match.kickoff_at.desc()).all()
        
        matches = []
        for match in raw_matches:
            if match.home_team is None or match.away_team is None:
                continue
            matches.append(match)
            if len(matches) >= limit:
                break
        
        if not matches:
            logger.warning("No finished matches found for auditing.")
            return
        
        # 各种统计指标容器
        v2_spf_brier_sum, v3_spf_brier_sum, v4_spf_brier_sum = 0.0, 0.0, 0.0
        v2_spf_rps_sum, v3_spf_rps_sum, v4_spf_rps_sum = 0.0, 0.0, 0.0
        v2_spf_correct, v3_spf_correct, v4_spf_correct = 0, 0, 0
        
        spf_valid_count = 0
        
        # 投注模拟 (EV > 5%)
        v2_bet_total, v2_bet_return = 0.0, 0.0
        v3_bet_total, v3_bet_return = 0.0, 0.0
        v4_bet_total, v4_bet_return = 0.0, 0.0
        
        detail_records = []
        
        for match in matches:
            # 读取 SPF 预测
            preds = db.query(Prediction).filter(
                Prediction.match_id == match.id,
                Prediction.play_type == PlayType.SPF
            ).all()
            
            v2_pred = next((p for p in preds if p.model_version == "v2.0"), None)
            v3_pred = next((p for p in preds if p.model_version == "v3.0"), None) or next((p for p in preds if p.model_version == "v3.0_shadow"), None)
            v4_pred = next((p for p in preds if p.model_version == "v4.0"), None)
            
            if not v2_pred or not v3_pred or not v4_pred:
                continue
                
            v2_probs = v2_pred.probabilities if isinstance(v2_pred.probabilities, dict) else json.loads(v2_pred.probabilities)
            v3_probs = v3_pred.probabilities if isinstance(v3_pred.probabilities, dict) else json.loads(v3_pred.probabilities)
            v4_probs = v4_pred.probabilities if isinstance(v4_pred.probabilities, dict) else json.loads(v4_pred.probabilities)
            
            actual_spf = match.actual_outcome  # "home", "draw", "away"
            
            # 计算 SPF 指标
            v2_spf_b = calculate_brier_score(v2_probs, actual_spf)
            v3_spf_b = calculate_brier_score(v3_probs, actual_spf)
            v4_spf_b = calculate_brier_score(v4_probs, actual_spf)
            
            v2_spf_r = calculate_rps(v2_probs, actual_spf)
            v3_spf_r = calculate_rps(v3_probs, actual_spf)
            v4_spf_r = calculate_rps(v4_probs, actual_spf)
            
            v2_spf_brier_sum += v2_spf_b
            v3_spf_brier_sum += v3_spf_b
            v4_spf_brier_sum += v4_spf_b
            
            v2_spf_rps_sum += v2_spf_r
            v3_spf_rps_sum += v3_spf_r
            v4_spf_rps_sum += v4_spf_r
            
            v2_spf_choice = max(v2_probs, key=v2_probs.get)
            v3_spf_choice = max(v3_probs, key=v3_probs.get)
            v4_spf_choice = max(v4_probs, key=v4_probs.get)
            
            if v2_spf_choice == actual_spf:
                v2_spf_correct += 1
            if v3_spf_choice == actual_spf:
                v3_spf_correct += 1
            if v4_spf_choice == actual_spf:
                v4_spf_correct += 1
                
            spf_valid_count += 1
            
            # 投注模拟
            odds_h = getattr(match, "odds_home", None) or getattr(match, "closing_odds_home", None)
            odds_d = getattr(match, "odds_draw", None) or getattr(match, "closing_odds_draw", None)
            odds_a = getattr(match, "odds_away", None) or getattr(match, "closing_odds_away", None)
            
            if odds_h and odds_d and odds_a:
                odds = {"home": odds_h, "draw": odds_d, "away": odds_a}
                
                # v2
                v2_evs = {k: v2_probs.get(k, 0.0) * odds[k] - 1.0 for k in ["home", "draw", "away"]}
                v2_best = max(v2_evs, key=v2_evs.get)
                if v2_evs[v2_best] > 0.05:
                    v2_bet_total += 1.0
                    if v2_best == actual_spf:
                        v2_bet_return += odds[v2_best]
                
                # v3
                v3_evs = {k: v3_probs.get(k, 0.0) * odds[k] - 1.0 for k in ["home", "draw", "away"]}
                v3_best = max(v3_evs, key=v3_evs.get)
                if v3_evs[v3_best] > 0.05:
                    v3_bet_total += 1.0
                    if v3_best == actual_spf:
                        v3_bet_return += odds[v3_best]
                        
                # v4
                v4_evs = {k: v4_probs.get(k, 0.0) * odds[k] - 1.0 for k in ["home", "draw", "away"]}
                v4_best = max(v4_evs, key=v4_evs.get)
                if v4_evs[v4_best] > 0.05:
                    v4_bet_total += 1.0
                    if v4_best == actual_spf:
                        v4_bet_return += odds[v4_best]
                        
            # 保存单场对比
            detail_records.append({
                "match_code": match.match_code,
                "teams": f"{match.home_team.name} vs {match.away_team.name}" if match.home_team and match.away_team else f"Match {match.id}",
                "actual_spf": actual_spf,
                "v2_spf": {k: f"{v*100:.1f}%" for k, v in v2_probs.items()},
                "v3_spf": {k: f"{v*100:.1f}%" for k, v in v3_probs.items()},
                "v4_spf": {k: f"{v*100:.1f}%" for k, v in v4_probs.items()}
            })

        if spf_valid_count == 0:
            logger.warning("No valid predictions found.")
            return

        # 计算平均值
        v2_spf_brier_avg = v2_spf_brier_sum / spf_valid_count
        v3_spf_brier_avg = v3_spf_brier_sum / spf_valid_count
        v4_spf_brier_avg = v4_spf_brier_sum / spf_valid_count
        
        v2_spf_rps_avg = v2_spf_rps_sum / spf_valid_count
        v3_spf_rps_avg = v3_spf_rps_sum / spf_valid_count
        v4_spf_rps_avg = v4_spf_rps_sum / spf_valid_count
        
        v2_spf_acc = v2_spf_correct / spf_valid_count
        v3_spf_acc = v3_spf_correct / spf_valid_count
        v4_spf_acc = v4_spf_correct / spf_valid_count
        
        v2_roi = (v2_bet_return - v2_bet_total) / v2_bet_total if v2_bet_total > 0 else 0.0
        v3_roi = (v3_bet_return - v3_bet_total) / v3_bet_total if v3_bet_total > 0 else 0.0
        v4_roi = (v4_bet_return - v4_bet_total) / v4_bet_total if v4_bet_total > 0 else 0.0
        
        # 打印审计摘要
        print("\n" + "="*80)
        print(f"MULTI-TIER ENGINE AUDIT REPORT (Sample Size: {spf_valid_count})")
        print("="*80)
        print(f"1. PlayType: SPF (胜平负)")
        print(f"   Metric         |  v2.0 (StackingNet) |  v3.0 (Mixed aligned) |  v4.0 (Deep Frontier)")
        print(f"   ---------------|---------------------|-----------------------|----------------------")
        print(f"   Brier Score    |  {v2_spf_brier_avg:.5f}            |  {v3_spf_brier_avg:.5f}              |  {v4_spf_brier_avg:.5f} (lower is better)")
        print(f"   RPS Score      |  {v2_spf_rps_avg:.5f}            |  {v3_spf_rps_avg:.5f}              |  {v4_spf_rps_avg:.5f} (lower is better)")
        print(f"   Accuracy (ACC) |  {v2_spf_acc:.2%} ({v2_spf_correct}/{spf_valid_count})  |  {v3_spf_acc:.2%} ({v3_spf_correct}/{spf_valid_count})    |  {v4_spf_acc:.2%} ({v4_spf_correct}/{spf_valid_count})")
        print(f"   ---------------|---------------------|-----------------------|----------------------")
        print(f"   v4.0 vs v2.0 Accuracy Change: {v4_spf_acc - v2_spf_acc:+.2%}")
        
        print("\n" + f"2. Simulated Betting Performance (EV > 5%)")
        print(f"   Strategy       |  Total Bets  |  Net Profit/Loss  |  ROI")
        print(f"   ---------------|--------------|-------------------|---------")
        print(f"   v2.0           |  {v2_bet_total:.1f}         |  {v2_bet_return - v2_bet_total:+.2f}              | {v2_roi:+.2%}")
        print(f"   v3.0 (Aligned) |  {v3_bet_total:.1f}         |  {v3_bet_return - v3_bet_total:+.2f}              | {v3_roi:+.2%}")
        print(f"   v4.0 (Deep)    |  {v4_bet_total:.1f}         |  {v4_bet_return - v4_bet_total:+.2f}              | {v4_roi:+.2%}")
        print(f"   ---------------|--------------|-------------------|---------")
        print("="*80)
        
        # 写入 JSON
        audit_output_path = os.path.join(_backend_root, "data", "shadow_audit_result.json")
        os.makedirs(os.path.dirname(audit_output_path), exist_ok=True)
        
        report_data = {
            "audited_at": datetime.now(timezone.utc).isoformat(),
            "sample_size": spf_valid_count,
            "metrics": {
                "spf": {
                    "v2": {"brier": v2_spf_brier_avg, "rps": v2_spf_rps_avg, "accuracy": v2_spf_acc},
                    "v3": {"brier": v3_spf_brier_avg, "rps": v3_spf_rps_avg, "accuracy": v3_spf_acc},
                    "v4": {"brier": v4_spf_brier_avg, "rps": v4_spf_rps_avg, "accuracy": v4_spf_acc}
                }
            },
            "betting": {
                "v2": {"total_bets": v2_bet_total, "profit": v2_bet_return - v2_bet_total, "roi": v2_roi},
                "v3": {"total_bets": v3_bet_total, "profit": v3_bet_return - v3_bet_total, "roi": v3_roi},
                "v4": {"total_bets": v4_bet_total, "profit": v4_bet_return - v4_bet_total, "roi": v4_roi}
            },
            "details": detail_records[:20]
        }
        with open(audit_output_path, "w", encoding="utf-8") as f:
            json.dump(report_data, f, indent=2, ensure_ascii=False)
        print(f"Audit report successfully written to {audit_output_path}")

    except Exception as e:
        logger.error(f"Audit error: {e}", exc_info=True)
    finally:
        db.close()

if __name__ == "__main__":
    limit_num = 200
    if len(sys.argv) > 1:
        try:
            limit_num = int(sys.argv[1])
        except ValueError:
            pass
    run_audit(limit_num)
