import sys, os
_root = os.getcwd()
for d in ["api", "core", "features", "ingestion", "database", "strategy", "monitor", "utils"]:
    sys.path.append(os.path.join(_root, d))

from database.models import SessionLocal, engine
from sqlalchemy import text

db = SessionLocal()
tables = ["teams", "matches", "jingcai_issues", "jingcai_issue_matches", "odds_history", "predictions", "prediction_steps", "accuracy_snapshots", "users"]

for table in tables:
    try:
        sql = f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), COALESCE((SELECT MAX(id)+1 FROM {table}), 1), false);"
        db.execute(text(sql))
        db.commit()
        print(f"Updated sequence for {table}")
    except Exception as e:
        db.rollback()
        print(f"Failed for {table}: {e}")
db.close()
