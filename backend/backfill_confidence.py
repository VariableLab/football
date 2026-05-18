"""回填已有预测的置信度（分批处理）"""
import sys, logging
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s", stream=sys.stdout)
from models import SessionLocal, Prediction, Match
from sqlalchemy import text

def compute_confidence(spf: dict, match) -> str:
    max_prob = max(spf.values())
    market = {}
    if match and match.odds_home and match.odds_draw and match.odds_away:
        inv = lambda o: 1.0 / o if o and o > 0 else 0
        total = inv(match.odds_home) + inv(match.odds_draw) + inv(match.odds_away)
        if total > 0:
            market = {"home": inv(match.odds_home)/total, "draw": inv(match.odds_draw)/total, "away": inv(match.odds_away)/total}
    has_market = len(market) > 0
    if not has_market:
        return "medium" if max_prob >= 0.65 else "low"
    disagreement = sum(abs(spf.get(k, 0) - market.get(k, 0)) for k in spf) / 2
    if disagreement > 0.12:
        return "low"
    if disagreement > 0.06:
        return "medium"
    if max_prob >= 0.60:
        return "high"
    if max_prob >= 0.45:
        return "medium"
    return "low"

def main():
    s = SessionLocal()
    try:
        # 先统计总数
        total = s.query(Prediction).filter(Prediction.confidence.is_(None)).count()
        print(f"待回填: {total} 条")

        stats = {"high": 0, "medium": 0, "low": 0}
        updated = 0
        BATCH = 5000
        offset = 0

        while True:
            batch = s.query(Prediction).filter(Prediction.confidence.is_(None)).limit(BATCH).offset(offset).all()
            if not batch:
                break
            for p in batch:
                match = s.get(Match, p.match_id)
                if not match:
                    continue
                conf = compute_confidence(p.probabilities, match)
                p.confidence = conf
                stats[conf] += 1
                updated += 1
            offset += BATCH
            s.commit()
            print(f"  进度: {updated}/{total}  high={stats['high']} medium={stats['medium']} low={stats['low']}")

        print(f"完成: 更新 {updated}")
        print(f"分布: {stats}")
    finally:
        s.close()

if __name__ == "__main__":
    main()
