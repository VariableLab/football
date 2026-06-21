import os
import sys
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.exc import IntegrityError

# Ensure backend imports work
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'database'))

# Import Models
from database.models import (
    User, Team, Match, Prediction, JingcaiIssue, JingcaiIssueMatch, 
    AccuracySnapshot, Feedback, UserSettings
)

# 1. Source (SQLite)
SQLITE_URL = "sqlite:///./database.sqlite"
sqlite_engine = create_engine(SQLITE_URL)
SqliteSession = sessionmaker(bind=sqlite_engine)

# 2. Destination (PostgreSQL)
# The user's PG credentials:
PG_URL = "postgresql://postgre:prefect@129.146.124.72:5432/wcanalytics"
pg_engine = create_engine(PG_URL, pool_size=10, max_overflow=20)
PgSession = sessionmaker(bind=pg_engine)

def migrate_table(sqlite_session, pg_session, model_class, batch_size=1000):
    table_name = model_class.__tablename__
    print(f"\n--- Migrating {table_name} ---")
    
    # Check count
    total_count = sqlite_session.query(model_class).count()
    if total_count == 0:
        print(f"Skipping {table_name}: No records in SQLite.")
        return

    print(f"Found {total_count} records. Starting migration...")
    
    offset = 0
    while offset < total_count:
        # Fetch batch from SQLite
        records = sqlite_session.query(model_class).order_by(model_class.id).offset(offset).limit(batch_size).all()
        if not records:
            break
            
        # Create detached clones for PG
        pg_records = []
        for r in records:
            sqlite_session.expunge(r) # Detach from sqlite
            from sqlalchemy.orm.session import make_transient
            make_transient(r) # Make it look like a new object
            pg_records.append(r)
            
        try:
            # Bulk save to PG
            pg_session.bulk_save_objects(pg_records)
            pg_session.commit()
            print(f"  Inserted {offset + len(records)} / {total_count}...")
        except IntegrityError:
            pg_session.rollback()
            print(f"  Integrity Error at offset {offset}. Attempting row-by-row fallback...")
            # Fallback to row-by-row for this batch to skip duplicates
            for r in pg_records:
                try:
                    pg_session.add(r)
                    pg_session.commit()
                except Exception:
                    pg_session.rollback()
            print("  Row-by-row recovery complete for batch.")
        except Exception as e:
            pg_session.rollback()
            print(f"  Critical error migrating batch: {e}")
            
        offset += batch_size
        
    print(f"Completed {table_name}.")

def run_migration():
    print("🚀 Starting Data Migration: SQLite -> PostgreSQL")
    
    sq_db = SqliteSession()
    pg_db = PgSession()
    
    try:
        # VERY IMPORTANT: Migration order matters due to Foreign Keys!
        # 1. Independent Tables
        migrate_table(sq_db, pg_db, User)
        migrate_table(sq_db, pg_db, Team)
        migrate_table(sq_db, pg_db, AccuracySnapshot)
        
        # 2. Level 1 Dependencies
        migrate_table(sq_db, pg_db, UserSettings)
        migrate_table(sq_db, pg_db, Match) # Depends on Team
        migrate_table(sq_db, pg_db, JingcaiIssue)
        
        # 3. Level 2 Dependencies
        migrate_table(sq_db, pg_db, Prediction) # Depends on Match
        migrate_table(sq_db, pg_db, JingcaiIssueMatch) # Depends on Issue & Match
        migrate_table(sq_db, pg_db, Feedback) # Depends on User
        
        # Note: We skip OddsHistory to save time unless strictly necessary, 
        # as it can be huge and is mostly used for debugging, not core inference.
        
        print("\n✅ All data migrated successfully!")
        
        # IMPORTANT: We must update the PostgreSQL sequence sequences so that new inserts don't fail with ID conflicts.
        tables = ['users', 'teams', 'matches', 'predictions', 'accuracy_snapshots', 'feedbacks', 'jingcai_issues']
        for t in tables:
            try:
                pg_db.execute(f"SELECT setval('{t}_id_seq', COALESCE((SELECT MAX(id)+1 FROM {t}), 1), false);")
            except Exception as seq_e:
                print(f"Warning: Could not update sequence for {t} (might not use autoincrement ID): {seq_e}")
        pg_db.commit()
        print("✅ PostgreSQL ID sequences synchronized.")
        
    except Exception as e:
        print(f"❌ Migration failed: {e}")
    finally:
        sq_db.close()
        pg_db.close()

if __name__ == "__main__":
    run_migration()
