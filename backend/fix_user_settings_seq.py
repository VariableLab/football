import sys, os
_root = os.getcwd()
for d in ["api", "core", "features", "ingestion", "database", "strategy", "monitor", "utils"]:
    sys.path.append(os.path.join(_root, d))

from database.models import SessionLocal
from sqlalchemy import text

db = SessionLocal()
try:
    # Use proper Python string formatting to avoid escaping hell
    sql = "SELECT setval(pg_get_serial_sequence('user_settings', 'id'), COALESCE((SELECT MAX(id)+1 FROM user_settings), 1), false);"
    db.execute(text(sql))
    db.commit()
    print("Updated sequence for user_settings")
except Exception as e:
    db.rollback()
    print(f"Failed for user_settings: {e}")
finally:
    db.close()
