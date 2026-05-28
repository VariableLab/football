
import asyncio
import os
import sys

# Ensure backend imports work
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from database.models import Base, engine
from database.config import get_settings

def create_pg_tables():
    print(f"Creating tables in PostgreSQL database: {get_settings().DATABASE_URL}")
    try:
        # Drop all tables first for a clean slate during this dev transition
        # CAUTION: In production, we would use Alembic. Since this is an empty DB, we can drop/create safely.
        Base.metadata.drop_all(bind=engine)
        print("Dropped old tables (if any).")
        
        Base.metadata.create_all(bind=engine)
        print("✅ All tables created successfully!")
    except Exception as e:
        print(f"❌ Failed to create tables: {e}")

if __name__ == "__main__":
    create_pg_tables()
