import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database.config as c
from sqlalchemy import create_engine, text

def run_migration():
    engine = create_engine(c.get_settings().DATABASE_URL)
    conn = engine.connect()

    ddls = [
        'ALTER TABLE teams ALTER COLUMN stats_synced_at TYPE timestamptz',
        'ALTER TABLE matches ALTER COLUMN kickoff_at TYPE timestamptz',
        'ALTER TABLE matches ALTER COLUMN created_at TYPE timestamptz',
        'ALTER TABLE matches ALTER COLUMN updated_at TYPE timestamptz',
        'ALTER TABLE matches ALTER COLUMN odds_locked_at TYPE timestamptz',
        'ALTER TABLE matches ALTER COLUMN opening_odds_at TYPE timestamptz',
        'ALTER TABLE predictions ALTER COLUMN locked_at TYPE timestamptz',
        'ALTER TABLE odds_history ALTER COLUMN recorded_at TYPE timestamptz',
        'ALTER TABLE auto_learning_log ALTER COLUMN learned_at TYPE timestamptz',
        'ALTER TABLE jingcai_issues ALTER COLUMN sale_start TYPE timestamptz',
        'ALTER TABLE jingcai_issues ALTER COLUMN sale_end TYPE timestamptz',
        'ALTER TABLE jingcai_issues ALTER COLUMN draw_at TYPE timestamptz',
        'ALTER TABLE jingcai_issues ALTER COLUMN created_at TYPE timestamptz',
        'ALTER TABLE jingcai_issues ALTER COLUMN updated_at TYPE timestamptz',
        'ALTER TABLE license_keys ALTER COLUMN created_at TYPE timestamptz',
        'ALTER TABLE license_keys ALTER COLUMN used_at TYPE timestamptz',
        'ALTER TABLE redeemed_licenses ALTER COLUMN redeemed_at TYPE timestamptz',
        'ALTER TABLE users ALTER COLUMN paid_until TYPE timestamptz',
        'ALTER TABLE users ALTER COLUMN created_at TYPE timestamptz',
        'ALTER TABLE users ALTER COLUMN updated_at TYPE timestamptz',
        'ALTER TABLE feedback ALTER COLUMN created_at TYPE timestamptz',
        'ALTER TABLE feedback_likes ALTER COLUMN created_at TYPE timestamptz',
        'ALTER TABLE system_settings ALTER COLUMN updated_at TYPE timestamptz',
        'ALTER TABLE model_checkpoints ALTER COLUMN deployed_at TYPE timestamptz'
    ]

    trans = conn.begin()
    try:
        for ddl in ddls:
            print(f'Running: {ddl}')
            conn.execute(text(ddl))
        trans.commit()
        print('✅ Schema migrated successfully!')
    except Exception as e:
        trans.rollback()
        print(f'❌ Migration failed: {e}')
        sys.exit(1)

if __name__ == "__main__":
    run_migration()
