#!/usr/bin/env python3
"""Migrate remaining small tables from SQLite to PostgreSQL."""
import sqlite3
import psycopg2
import re
import os

PG_DSN = os.environ.get("DATABASE_URL", "postgresql://postgre:prefect@129.146.124.72:5432/football")
m = re.match(r'postgresql://([^:]+):([^@]+)@([^:]+):(\d+)/(.+)', PG_DSN)
pg = psycopg2.connect(host=m.group(3), port=int(m.group(4)),
                      database=m.group(5), user=m.group(1), password=m.group(2))
sqlite_conn = sqlite3.connect(os.path.join(os.path.dirname(__file__), "database.sqlite"))

bool_cols = {"is_active", "is_paid", "is_anonymous", "is_broadcasted", "is_closing", "is_real", "odds_degraded"}
col_map = {"group": "group_name"}

tables = ["feedbacks", "live_odds_snapshots", "audit_logs"]
for table in tables:
    sc = sqlite_conn.cursor()
    sc.execute(f"SELECT * FROM {table}")
    columns = [desc[0] for desc in sc.description]
    rows = sc.fetchall()
    sc.close()

    if not rows:
        print(f"  {table}: 0 rows (skip)")
        continue

    mapped_cols = [col_map.get(c, c) for c in columns]
    pc = pg.cursor()
    col_names = ", ".join(mapped_cols)

    val_specs = []
    for col in columns:
        if col in bool_cols:
            val_specs.append("%s::boolean")
        else:
            val_specs.append("%s")
    val_str = ", ".join(val_specs)
    insert_sql = f"INSERT INTO {table} ({col_names}) VALUES ({val_str})"

    batch_size = 1000
    for i in range(0, len(rows), batch_size):
        batch = rows[i:i + batch_size]
        pc.executemany(insert_sql, batch)
    pg.commit()
    pc.close()
    print(f"  {table}: {len(rows)} rows migrated")

sqlite_conn.close()
pg.close()
print("Done!")
