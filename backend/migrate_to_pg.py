#!/usr/bin/env python3
"""
SQLite → PostgreSQL 全量迁移脚本

用法:
    python migrate_to_pg.py          # 创建表结构
    python migrate_to_pg.py --migrate  # 导出数据并导入 PG
    python migrate_to_pg.py --verify   # 验证数据完整性

环境变量:
    PG_HOST=129.146.124.72
    PG_PORT=5432
    PG_USER=postgre
    PG_PASSWORD=prefect
    PG_DB=football
"""
import sys
import os
import json
import argparse
import sqlite3
from datetime import datetime
from typing import Dict, Any, List

import psycopg2
import psycopg2.extras

# ─── 配置 ───
import argparse
DB_PATH = os.path.join(os.path.dirname(__file__), "database.sqlite")
parser = argparse.ArgumentParser()
parser.add_argument('--sqlite', default=DB_PATH, help='Path to SQLite database')
args = parser.parse_args()
DB_PATH = args.sqlite
PG_DSN = os.environ.get("DATABASE_URL", "")

if not PG_DSN:
    PG_DSN = (
        f"postgresql://{os.environ.get('PG_USER', 'postgre')}:"
        f"{os.environ.get('PG_PASSWORD', 'prefect')}"
        f"@{os.environ.get('PG_HOST', '129.146.124.72')}:"
        f"{os.environ.get('PG_PORT', '5432')}"
        f"/{os.environ.get('PG_DB', 'football')}"
    )


def get_sqlite_conn():
    return sqlite3.connect(DB_PATH)


def get_pg_conn():
    import re
    # Parse DSN: postgresql://user:pass@host:port/db
    m = re.match(
        r"postgresql://([^:]+):([^@]+)@([^:]+):(\d+)/(.+)", PG_DSN
    )
    if not m:
        raise ValueError(f"Invalid DATABASE_URL: {PG_DSN}")
    return psycopg2.connect(
        host=m.group(3), port=int(m.group(4)),
        database=m.group(5), user=m.group(1), password=m.group(2),
    )


# ─── 表结构定义 ───
TABLES_SQL = """
CREATE TABLE IF NOT EXISTS teams (
    id SERIAL PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    name_en VARCHAR(100),
    code VARCHAR(10) UNIQUE NOT NULL,
    flag VARCHAR(10),
    fifa_rank INTEGER,
    elo INTEGER,
    "group_name" VARCHAR(10),
    continent VARCHAR(50),
    squad_size INTEGER,
    form_last5 VARCHAR(10),
    form_factor FLOAT,
    avg_goals_scored FLOAT,
    avg_goals_conceded FLOAT,
    recent_results VARCHAR(20),
    recent_goals_scored FLOAT,
    recent_goals_conceded FLOAT,
    home_away_factor FLOAT,
    weather_adaptability FLOAT,
    tactical_style VARCHAR(20),
    coach_rating FLOAT,
    rest_days INTEGER,
    key_injuries VARCHAR(200),
    squad_fatigue_index FLOAT DEFAULT 0.5,
    avg_xg FLOAT,
    avg_xga FLOAT,
    possession FLOAT,
    pass_completion FLOAT,
    shots_per_game FLOAT,
    stats_synced_at TIMESTAMPTZ,
    key_players_available INTEGER DEFAULT 11,
    key_players_total INTEGER DEFAULT 11
);

CREATE TABLE IF NOT EXISTS matches (
    id SERIAL PRIMARY KEY,
    match_code VARCHAR(50) UNIQUE NOT NULL,
    home_team_id INTEGER REFERENCES teams(id),
    away_team_id INTEGER REFERENCES teams(id),
    kickoff_at TIMESTAMPTZ,
    "group_name" VARCHAR(10),
    stage VARCHAR(50),
    venue VARCHAR(100),
    match_type VARCHAR(9),
    competition VARCHAR(100),
    status VARCHAR(9),
    odds_home FLOAT,
    odds_draw FLOAT,
    odds_away FLOAT,
    actual_home_goals INTEGER,
    actual_away_goals INTEGER,
    actual_outcome VARCHAR(10),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ,
    confidence VARCHAR(10),
    odds_source VARCHAR(20),
    venue_type VARCHAR(20),
    weather VARCHAR(20),
    temperature FLOAT,
    pitch_condition VARCHAR(20),
    schedule_density VARCHAR(20),
    closing_odds_home FLOAT,
    closing_odds_draw FLOAT,
    closing_odds_away FLOAT,
    closing_odds_source VARCHAR(20),
    odds_locked_at TIMESTAMPTZ,
    ht_home_goals INTEGER,
    ht_away_goals INTEGER,
    opening_odds_home FLOAT,
    opening_odds_draw FLOAT,
    opening_odds_away FLOAT,
    opening_odds_source VARCHAR(100),
    opening_odds_at TIMESTAMPTZ,
    odds_degraded BOOLEAN DEFAULT FALSE,
    poster_url VARCHAR(500),
    is_broadcasted BOOLEAN DEFAULT FALSE
);

CREATE INDEX IF NOT EXISTS idx_matches_status ON matches(status);
CREATE INDEX IF NOT EXISTS idx_matches_kickoff ON matches(kickoff_at);
CREATE INDEX IF NOT EXISTS idx_matches_competition ON matches(competition);

CREATE TABLE IF NOT EXISTS predictions (
    id SERIAL PRIMARY KEY,
    match_id INTEGER REFERENCES matches(id),
    play_type VARCHAR(5) NOT NULL,
    probabilities JSONB NOT NULL,
    model_version VARCHAR(20),
    input_checksum VARCHAR(64),
    locked_at TIMESTAMPTZ DEFAULT NOW(),
    confidence VARCHAR(10)
);

CREATE INDEX IF NOT EXISTS idx_predictions_match ON predictions(match_id);
CREATE INDEX IF NOT EXISTS idx_predictions_play ON predictions(play_type);

CREATE TABLE IF NOT EXISTS odds_history (
    id SERIAL PRIMARY KEY,
    match_id INTEGER REFERENCES matches(id),
    source VARCHAR(20) NOT NULL,
    odds_home FLOAT NOT NULL,
    odds_draw FLOAT NOT NULL,
    odds_away FLOAT NOT NULL,
    recorded_at TIMESTAMPTZ NOT NULL,
    is_closing BOOLEAN,
    is_real BOOLEAN
);

CREATE INDEX IF NOT EXISTS idx_odds_history_match ON odds_history(match_id);

CREATE TABLE IF NOT EXISTS fusion_weights (
    id SERIAL PRIMARY KEY,
    stage VARCHAR(20),
    elo_diff_range VARCHAR(20),
    weights JSONB NOT NULL,
    metric VARCHAR(20),
    metric_value FLOAT,
    sample_size INTEGER,
    is_active BOOLEAN,
    learned_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS jingcai_issues (
    id SERIAL PRIMARY KEY,
    issue_id VARCHAR(20) NOT NULL,
    issue_type VARCHAR(20),
    status VARCHAR(20),
    sale_start TIMESTAMPTZ,
    sale_end TIMESTAMPTZ,
    draw_at TIMESTAMPTZ,
    draw_result JSONB,
    verification JSONB,
    notes TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS jingcai_issue_matches (
    id SERIAL PRIMARY KEY,
    issue_id INTEGER REFERENCES jingcai_issues(id),
    match_id INTEGER REFERENCES matches(id),
    sequence INTEGER,
    handicap INTEGER,
    rq_odds TEXT,
    score_odds TEXT,
    goals_odds TEXT,
    half_odds TEXT
);

CREATE TABLE IF NOT EXISTS teams (
    id SERIAL PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    name_en VARCHAR(100),
    code VARCHAR(10) UNIQUE NOT NULL,
    flag VARCHAR(10),
    fifa_rank INTEGER,
    elo INTEGER,
    "group_name" VARCHAR(10),
    continent VARCHAR(50),
    squad_size INTEGER,
    form_last5 VARCHAR(10),
    form_factor FLOAT,
    avg_goals_scored FLOAT,
    avg_goals_conceded FLOAT,
    recent_results VARCHAR(20),
    recent_goals_scored FLOAT,
    recent_goals_conceded FLOAT,
    home_away_factor FLOAT,
    weather_adaptability FLOAT,
    tactical_style VARCHAR(20),
    coach_rating FLOAT,
    rest_days INTEGER,
    key_injuries VARCHAR(200),
    squad_fatigue_index FLOAT DEFAULT 0.5,
    avg_xg FLOAT,
    avg_xga FLOAT,
    possession FLOAT,
    pass_completion FLOAT,
    shots_per_game FLOAT,
    stats_synced_at TIMESTAMPTZ,
    key_players_available INTEGER DEFAULT 11,
    key_players_total INTEGER DEFAULT 11
);

-- Additional tables (may be empty)
CREATE TABLE IF NOT EXISTS accuracy_snapshots (
    id SERIAL PRIMARY KEY,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    direction_accuracy FLOAT,
    high_conf_accuracy FLOAT,
    avg_brier_score FLOAT,
    avg_log_loss FLOAT,
    avg_max_prob FLOAT,
    total_matches INTEGER,
    data JSONB
);

CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    email VARCHAR(255) UNIQUE,
    password_hash VARCHAR(255),
    is_active BOOLEAN DEFAULT TRUE,
    is_paid BOOLEAN DEFAULT FALSE,
    paid_until TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS feedbacks (
    id SERIAL PRIMARY KEY,
    category VARCHAR(50),
    match_id INTEGER,
    content TEXT,
    is_anonymous BOOLEAN DEFAULT FALSE,
    likes INTEGER DEFAULT 0,
    author VARCHAR(100),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS live_odds_snapshots (
    id SERIAL PRIMARY KEY,
    match_id INTEGER,
    source VARCHAR(20),
    odds_home FLOAT,
    odds_draw FLOAT,
    odds_away FLOAT,
    match_minute INTEGER,
    score_home INTEGER,
    score_away INTEGER,
    recorded_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS audit_logs (
    id SERIAL PRIMARY KEY,
    action VARCHAR(100),
    details JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ
);
"""


def create_pg_tables(pg_conn):
    """创建 PostgreSQL 表结构和索引"""
    cur = pg_conn.cursor()
    cur.execute(TABLES_SQL)
    pg_conn.commit()
    cur.close()
    print("✅ PostgreSQL tables created")


def migrate_table(sqlite_conn, pg_conn, table: str, pg_table: str = None):
    """迁移单个表"""
    if pg_table is None:
        pg_table = table

    # 读取 SQLite 数据
    cur = sqlite_conn.cursor()
    cur.execute(f"SELECT * FROM {table}")
    columns = [desc[0] for desc in cur.description]
    rows = cur.fetchall()
    cur.close()

    if not rows:
        print(f"  ⏭️  {table}: 0 rows (skip)")
        return

    # Map reserved words
    col_map = {"group": "group_name", "order": "order", "values": "values"}
    mapped_cols = [col_map.get(c, c) for c in columns]
    
    pg_cur = pg_conn.cursor()
    placeholders = ", ".join(["%s"] * len(columns))
    col_names = ", ".join(mapped_cols)
    bool_cols = {"odds_degraded", "is_broadcasted", "is_closing", "is_real", "is_active", "is_paid"}
    # Cast boolean columns
    val_specs = []
    for col in columns:
        if col in bool_cols:
            val_specs.append(f"%s::boolean")
        else:
            val_specs.append("%s")
    val_str = ", ".join(val_specs)
    insert_sql = f"INSERT INTO {pg_table} ({col_names}) VALUES ({val_str})"
    batch_size = 1000
    for i in range(0, len(rows), batch_size):
        batch = rows[i:i + batch_size]
        # Debug first row
        if i == 0 and batch:
            first_row = batch[0]
            print(f"  DEBUG {table}: row_len={len(first_row)}, placeholders={insert_sql.count(chr(37))}")
        pg_cur.executemany(insert_sql, batch)

    pg_conn.commit()
    pg_cur.close()
    print(f"  ✅ {table}: {len(rows)} rows migrated")


def main():
    parser = argparse.ArgumentParser(description="SQLite → PostgreSQL migration")
    parser.add_argument("--create-tables", action="store_true", help="Create PG tables only")
    parser.add_argument("--migrate", action="store_true", help="Migrate data")
    parser.add_argument("--verify", action="store_true", help="Verify data integrity")
    parser.add_argument("--all", action="store_true", help="Create tables + migrate + verify")
    args = parser.parse_args()

    if not any([args.create_tables, args.migrate, args.verify, args.all]):
        args.all = True

    print(f"SQLite DB: {DB_PATH}")
    print(f"PostgreSQL: {PG_DSN}")
    print()

    sqlite_conn = get_sqlite_conn()
    pg_conn = get_pg_conn()

    if args.create_tables or args.all:
        create_pg_tables(pg_conn)

    if args.migrate or args.all:
        # Migration order: teams → matches → predictions → odds → other
        tables_order = [
            ("teams", "teams"),
            ("matches", "matches"),
            ("predictions", "predictions"),
            ("odds_history", "odds_history"),
            ("fusion_weights", "fusion_weights"),
            ("jingcai_issues", "jingcai_issues"),
            ("jingcai_issue_matches", "jingcai_issue_matches"),
            ("accuracy_snapshots", "accuracy_snapshots"),
            ("users", "users"),
            ("feedbacks", "feedbacks"),
            ("live_odds_snapshots", "live_odds_snapshots"),
            ("audit_logs", "audit_logs"),
        ]

        for sqlite_table, pg_table in tables_order:
            try:
                migrate_table(sqlite_conn, pg_conn, sqlite_table, pg_table)
            except Exception as e:
                print(f"  ❌ {sqlite_table}: {e}")

    if args.verify or args.all:
        print("\n--- Verification ---")
        pg_cur = pg_conn.cursor()
        for table in ["teams", "matches", "predictions", "odds_history", 
                       "fusion_weights", "jingcai_issues", "jingcai_issue_matches"]:
            try:
                count = pg_cur.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                print(f"  {table}: {count} rows")
            except:
                print(f"  {table}: error")
        pg_cur.close()

    sqlite_conn.close()
    pg_conn.close()
    print("\n✅ Done!")


if __name__ == "__main__":
    main()
