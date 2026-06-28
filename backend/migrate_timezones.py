import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database.config as c
from sqlalchemy import create_engine, text, inspect

def run_migration():
    engine = create_engine(c.get_settings().DATABASE_URL)
    conn = engine.connect()
    inspector = inspect(engine)
    
    tables = inspector.get_table_names()
    print(f"Found {len(tables)} tables. Starting dynamic timezone migration...")
    
    trans = conn.begin()
    try:
        for table in tables:
            columns = inspector.get_columns(table)
            for col in columns:
                col_name = col['name']
                col_type = str(col['type']).upper()
                # Check if column is TIMESTAMP without timezone
                if 'TIMESTAMP' in col_type and 'TIMEZONE' not in col_type and 'TZ' not in col_type:
                    ddl = f'ALTER TABLE "{table}" ALTER COLUMN "{col_name}" TYPE timestamptz'
                    print(f"Altering: {table}.{col_name} ({col_type} -> TIMESTAMPTZ)")
                    conn.execute(text(ddl))
        trans.commit()
        print('✅ All timestamp columns successfully migrated to TIMESTAMPTZ!')
    except Exception as e:
        trans.rollback()
        print(f'❌ Dynamic migration failed: {e}')
        sys.exit(1)

if __name__ == "__main__":
    run_migration()
