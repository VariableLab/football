"""补录6期竞彩开奖结果"""
import sys, json, logging
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s", stream=sys.stdout)
from sqlalchemy import text
from database.models import SessionLocal, JingcaiIssue

s = SessionLocal()
issues = s.query(JingcaiIssue).filter(
    JingcaiIssue.status == 'drawn',
    JingcaiIssue.draw_result.is_(None)
).all()

for iss in issues:
    rows = s.execute(text('''
        SELECT m.match_code, m.actual_outcome, m.id, m.home_team_id, m.away_team_id
        FROM jingcai_issue_matches jim
        JOIN matches m ON m.id = jim.match_id
        WHERE jim.issue_id = :iid
        ORDER BY jim.sequence
    '''), {'iid': iss.id}).fetchall()

    results = []
    for r in rows:
        results.append({
            "match_code": r[0],
            "actual": r[1] if r[1] else "unknown",
            "match_id": r[2],
        })

    total = len(results)
    known = sum(1 for r in results if r["actual"] != "unknown")
    home_wins = sum(1 for r in results if r["actual"] == "home")
    draws = sum(1 for r in results if r["actual"] == "draw")
    away_wins = sum(1 for r in results if r["actual"] == "away")

    iss.draw_result = json.dumps({
        "results": [r["actual"] for r in results],
        "prizes": {}
    }, ensure_ascii=False)
    iss.verification = json.dumps({
        "total_matches": total,
        "known_results": known,
        "home_wins": home_wins,
        "draws": draws,
        "away_wins": away_wins,
        "pending": total - known,
    }, ensure_ascii=False)
    logging.info(f"{iss.issue_id}: {known}/{total} results filled (H={home_wins} D={draws} A={away_wins}, pending={total-known})")

s.commit()
s.close()
print("完成")
