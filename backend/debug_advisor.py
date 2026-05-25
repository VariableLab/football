import sys, os
_root = os.getcwd()
for d in ['api', 'core', 'features', 'ingestion', 'database', 'strategy', 'monitor', 'utils', 'api/routers']:
    sys.path.append(os.path.join(_root, d))

# Disable exception hook for raw traceback
import utils.logger
utils.logger.handle_exception = lambda *args: None

try:
    from api.routers.advisor import get_top_picks
    from database.models import SessionLocal
    db = SessionLocal()
    res = get_top_picks(db)
    print(f'Top Picks: {res}')
except Exception as e:
    import traceback
    traceback.print_exc()
