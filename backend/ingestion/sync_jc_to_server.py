#!/usr/bin/env python3
"""
本地同步脚本 — 本地跑 zgzcw 竞彩同步，将结果推送到服务器数据库

用法:
  python sync_jc_to_server.py                          # 同步+推送
  python sync_jc_to_server.py --sync-only              # 只同步本地，不推送
  python sync_jc_to_server.py --ssh-key ~/.ssh/xxx     # 指定密钥

前置条件:
  - 本地能访问 live.zgzcw.com
  - 服务器已配置 SSH 免密登录
  - 本地和服务器数据库结构一致
"""
import argparse
import os
import sqlite3
import subprocess
import sys
import tempfile
from datetime import datetime, timezone

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(PROJECT_ROOT, "database.sqlite")

# 需要同步的表（注意顺序：先 teams 再 matches，避免外键冲突）
SYNC_TABLES = [
    "teams",
    "matches",
    "jingcai_issues",
    "jingcai_issue_matches",
    "odds_history",
]

# SSH 配置 (优先从环境变量读取)
SSH_USER = os.environ.get("REMOTE_USER", "ubuntu")
SSH_HOST = os.environ.get("REMOTE_HOST", "129.146.124.72")
SSH_KEY = os.path.expanduser(os.environ.get("SSH_KEY_PATH", "~/.ssh/server_key"))
REMOTE_PROJECT = os.environ.get("REMOTE_PROJECT", "/home/ubuntu/Github/football")


def log(msg: str) -> None:
    print(f"  [{datetime.now().strftime('%H:%M:%S')}] {msg}")


def step(name: str) -> None:
    print(f"\n{'='*50}")
    print(f"  {name}")
    print(f"{'='*50}")


def run_local_sync() -> bool:
    step("1/3 本地 zgzcw 同步")
    # 💡 确保能够找到 backend 根目录模块
    _backend_dir = os.path.dirname(PROJECT_ROOT)
    if _backend_dir not in sys.path:
        sys.path.insert(0, _backend_dir)
        
    try:
        from database.models import Base
        from sqlalchemy import create_engine
        
        db_url = f"sqlite:///{os.path.abspath(DB_PATH)}"
        engine = create_engine(db_url)
        print(f"  Initializing local schema at {DB_PATH}...")
        Base.metadata.create_all(bind=engine)

        from ingestion.zgzcw_jc_sync import sync_jc_matches
        result = sync_jc_matches(DB_PATH)
        log(f"同步结果: {result['matches']} 场, 新增 {result['created']}, 更新 {result['updated']}, 关联 {result.get('issues_linked', 0)}")
        
        if result.get("errors", 0) > 0:
            log(f"⚠️ 同步过程中出现 {result['errors']} 个错误")
            if result["created"] == 0 and result["updated"] == 0:
                log("❌ 同步完全失败")
                return False
                
        log("✅ 本地同步步骤完成")
        return True
    except Exception as e:
        import traceback
        log(f"❌ 初始化或同步失败: {e}")
        traceback.print_exc()
        return False


def dump_sync_sql() -> str:
    """生成 INSERT OR REPLACE SQL"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    lines = ["BEGIN TRANSACTION;"]
    for table in SYNC_TABLES:
        cur.execute(f"SELECT * FROM {table}")
        rows = cur.fetchall()
        if not rows:
            continue
        columns = [desc[0] for desc in cur.description]
        col_names = ", ".join(f'"{c}"' for c in columns)
        placeholders = ", ".join("?" for _ in columns)
        values_list = [tuple(r) for r in rows]
        # 逐行 INSERT OR REPLACE
        for vals in values_list:
            escaped = []
            for v in vals:
                if v is None:
                    escaped.append("NULL")
                elif isinstance(v, (int, float)):
                    escaped.append(str(v))
                else:
                    escaped.append("'" + str(v).replace("'", "''") + "'")
            lines.append(f'INSERT OR REPLACE INTO "{table}" ({col_names}) VALUES ({", ".join(escaped)});')
    lines.append("COMMIT;")
    conn.close()
    return "\n".join(lines)


def push_to_server(dry_run: bool = False) -> bool:
    step("2/3 生成同步 SQL")
    sql = dump_sync_sql()
    log(f"生成 {len(sql.split(chr(10)))} 行 SQL")

    step("3/3 推送至服务器")
    with tempfile.NamedTemporaryFile(mode="w", suffix=".sql", delete=False) as f:
        f.write(sql)
        sql_path = f.name

    try:
        scp_sql_cmd = [
            "scp", "-i", SSH_KEY,
            "-o", "ConnectTimeout=10",
            "-o", "StrictHostKeyChecking=no",
            sql_path,
            f"{SSH_USER}@{SSH_HOST}:{REMOTE_PROJECT}/backend/_jc_sync_temp.sql",
        ]
        log(f"SCP 上传...")
        subprocess.run(scp_sql_cmd, check=True, capture_output=True)
        log("✅ 上传完成")

        if dry_run:
            log(f"⏭️  dry-run 模式，跳过执行")
            log(f"SQL 文件: {sql_path}")
            return True

        # 用 Python 执行导入（sqlite3 命令行不可用时用 venv 里的 Python）
        ssh_apply_cmd = [
            "ssh", "-i", SSH_KEY,
            "-o", "ConnectTimeout=10",
            "-o", "StrictHostKeyChecking=no",
            f"{SSH_USER}@{SSH_HOST}",
            f"cd {REMOTE_PROJECT}/backend && venv/bin/python -c 'import sqlite3; conn=sqlite3.connect(\"database.sqlite\"); conn.executescript(open(\"_jc_sync_temp.sql\").read()); conn.commit(); conn.close(); print(\"Applied\")' && rm _jc_sync_temp.sql"
        ]
        log(f"SSH 执行导入...")
        result = subprocess.run(ssh_apply_cmd, check=True, capture_output=True, text=True, timeout=30)
        log(f"✅ {result.stdout.strip()}")

        clean_cmd = [
            "ssh", "-i", SSH_KEY,
            "-o", "ConnectTimeout=10",
            f"{SSH_USER}@{SSH_HOST}",
            f"rm -f {REMOTE_PROJECT}/backend/_jc_sync_temp.sql",
        ]
        subprocess.run(clean_cmd, capture_output=True)
        log("✅ 服务器导入完成，临时文件已清理")
        return True

    except subprocess.CalledProcessError as e:
        log(f"❌ 推送失败: {e.stderr.decode() if hasattr(e.stderr, 'decode') else e}")
        return False
    finally:
        os.unlink(sql_path)


def verify_server() -> bool:
    """验证服务器同步结果"""
    step("验证服务器同步结果")
    ssh_cmd = [
        "ssh", "-i", SSH_KEY,
        "-o", "ConnectTimeout=10",
        f"{SSH_USER}@{SSH_HOST}",
        f"cd {REMOTE_PROJECT}/backend && "
        f"venv/bin/python -c \""
        f"import sqlite3; conn=sqlite3.connect('database.sqlite'); cur=conn.cursor(); "
        f"cur.execute('SELECT COUNT(*) FROM jingcai_issues'); issues=cur.fetchone()[0]; "
        f"cur.execute('SELECT COUNT(*) FROM jingcai_issue_matches'); links=cur.fetchone()[0]; "
        f"cur.execute('SELECT COUNT(*) FROM matches'); matches=cur.fetchone()[0]; "
        f"print(f'比赛{{matches}} 期号{{issues}} 关联{{links}}'); conn.close()"
        f"\"",
    ]
    try:
        result = subprocess.run(ssh_cmd, capture_output=True, text=True, timeout=15)
        log(f"📊 服务器数据: {result.stdout.strip()}")
        return True
    except Exception as e:
        log(f"❌ 验证失败: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(description="本地同步竞彩数据到服务器")
    parser.add_argument("--sync-only", action="store_true", help="只同步本地，不推送")
    parser.add_argument("--dry-run", action="store_true", help="生成 SQL 但不执行推送")
    parser.add_argument("--ssh-key", default="~/.ssh/server_key", help="SSH 私钥路径")
    args = parser.parse_args()

    global SSH_KEY
    SSH_KEY = os.path.expanduser(args.ssh_key)

    print(f"\n{'#'*50}")
    print(f"  竞彩数据同步工具")
    print(f"  {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print(f"  本地 → {SSH_USER}@{SSH_HOST}")
    print(f"{'#'*50}")

    ok = run_local_sync()
    if not ok:
        sys.exit(1)

    if args.sync_only:
        log("\n⏭️  --sync-only 模式，跳过推送")
        return

    ok = push_to_server(dry_run=args.dry_run)
    if not ok:
        sys.exit(1)

    verify_server()
    log("\n✅ 全部完成")


if __name__ == "__main__":
    main()
