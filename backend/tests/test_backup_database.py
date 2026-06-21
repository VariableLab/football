import os
import tempfile
import sqlite3
import json
from datetime import datetime, timedelta
from unittest.mock import patch
import pytest

# 确保导入可用 conftest 注入
from monitor.scheduler import backup_database_job, cleanup_old_backups
from scripts.backup_database import main as cli_main

@pytest.fixture
def temp_env():
    """创建测试用的临时源数据库和备份目录。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test_database.sqlite")
        backup_dir = os.path.join(tmpdir, "backup")
        
        # 初始化源 SQLite 数据库
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("CREATE TABLE test (id INTEGER PRIMARY KEY, val TEXT)")
        cursor.execute("INSERT INTO test (val) VALUES ('initial_data')")
        conn.commit()
        conn.close()
        
        yield db_path, backup_dir

def test_scenario1_basic_backup(temp_env):
    """场景 1：验证基础备份成功，生成备份文件和元数据 JSON 文件。"""
    db_path, backup_dir = temp_env
    
    result = backup_database_job(backup_dir=backup_dir, db_path=db_path)
    
    assert result["status"] == "ok"
    assert "path" in result
    assert os.path.exists(result["path"])
    
    # 验证元数据写出
    meta_path = os.path.join(backup_dir, ".backup_meta.json")
    assert os.path.exists(meta_path)
    with open(meta_path, "r") as f:
        meta = json.load(f)
        assert "last_hash" in meta
        assert "last_backup" in meta

def test_scenario2_hash_deduplication(temp_env):
    """场景 2：验证在源 DB 无变化时自动跳过备份（去重机制）。"""
    db_path, backup_dir = temp_env
    
    # 第一次备份成功
    res1 = backup_database_job(backup_dir=backup_dir, db_path=db_path)
    assert res1["status"] == "ok"
    
    # 数据库没变化，第二次备份跳过
    res2 = backup_database_job(backup_dir=backup_dir, db_path=db_path)
    assert res2["status"] == "skipped"
    assert res2["reason"] == "unchanged"

def test_scenario3_forced_backup(temp_env):
    """场景 3：验证通过 CLI --force 参数可以强制备份无变化的 DB。"""
    db_path, backup_dir = temp_env
    
    # 第一次备份
    res1 = backup_database_job(backup_dir=backup_dir, db_path=db_path)
    assert res1["status"] == "ok"
    
    # 调用 CLI 工具，使用 --force
    test_args = [
        "backup_database.py",
        "--db-path", db_path,
        "--backup-dir", backup_dir,
        "--force"
    ]
    with patch("sys.argv", test_args):
        rc = cli_main()
        assert rc == 0  # 强制备份成功返回 0

def test_scenario4_keep_daily(temp_env):
    """场景 4：验证日备保留策略（keep_daily）。"""
    _, backup_dir = temp_env
    os.makedirs(backup_dir, exist_ok=True)
    
    # 模拟过去 10 天的备份（每天一个，从最旧到最新）
    now = datetime.now()
    for i in range(10):
        t = now - timedelta(days=10 - i)
        ts_str = t.strftime("%Y%m%d_%H%M%S")
        fpath = os.path.join(backup_dir, f"db_{ts_str}.sqlite")
        with open(fpath, "w") as f:
            f.write("mock")
            
    # 只保留最近 5 天的
    cleanup_old_backups(backup_dir=backup_dir, keep_daily=5, keep_weekly=0, max_size_gb=1.0)
    
    files = [f for f in os.listdir(backup_dir) if f.startswith("db_") and f.endswith(".sqlite")]
    assert len(files) == 5

def test_scenario5_keep_weekly(temp_env):
    """场景 5：验证周备保留策略（保留周日备份）。"""
    _, backup_dir = temp_env
    os.makedirs(backup_dir, exist_ok=True)
    
    # 模拟在过去两周里的一批备份
    # 包含：连续 4 天的日备，以及两个跨度较远的周日备份
    # 2026-06-07 (周日), 2026-06-14 (周日)
    sunday1 = datetime(2026, 6, 7, 12, 0, 0)
    sunday2 = datetime(2026, 6, 14, 12, 0, 0)
    other_day1 = datetime(2026, 6, 15, 12, 0, 0)
    other_day2 = datetime(2026, 6, 16, 12, 0, 0)
    
    for dt in [sunday1, sunday2, other_day1, other_day2]:
        ts_str = dt.strftime("%Y%m%d_%H%M%S")
        fpath = os.path.join(backup_dir, f"db_{ts_str}.sqlite")
        with open(fpath, "w") as f:
            f.write("mock")
            
    # 日备保留 2 天 (应保留 6/15, 6/16)
    # 周备保留 2 个周日 (应额外保留 6/7, 6/14)
    cleanup_old_backups(backup_dir=backup_dir, keep_daily=2, keep_weekly=2, max_size_gb=1.0)
    
    files = [f for f in os.listdir(backup_dir) if f.startswith("db_") and f.endswith(".sqlite")]
    # 应保留这 4 个文件
    assert len(files) == 4
    for dt in [sunday1, sunday2, other_day1, other_day2]:
        ts_str = dt.strftime("%Y%m%d_%H%M%S")
        assert os.path.exists(os.path.join(backup_dir, f"db_{ts_str}.sqlite"))

def test_scenario6_max_size_limit(temp_env):
    """场景 6：验证容量上限（max_size_gb）清理逻辑。"""
    _, backup_dir = temp_env
    os.makedirs(backup_dir, exist_ok=True)
    
    # 我们创建 4 个各 5MB 的虚拟大备份文件 (共 20MB)
    # 20MB = ~0.0186 GB
    # 我们模拟文件的写操作，使用 seek 和 write 写入精确大小
    now = datetime.now()
    files_to_create = []
    for i in range(4):
        t = now - timedelta(days=4 - i)
        ts_str = t.strftime("%Y%m%d_%H%M%S")
        fpath = os.path.join(backup_dir, f"db_{ts_str}.sqlite")
        with open(fpath, "wb") as f:
            f.seek(5 * 1024 * 1024 - 1)  # 5MB
            f.write(b"\0")
        files_to_create.append(fpath)
            
    # 我们限制备份最大为 0.013 GB (~13.9 MB)，并且 keep_daily=10
    # 由于总容量超出了 0.013 GB，虽然日备允许保留 10 个，它仍然应当把最旧的文件逐个删除以满足容量上限
    # 4个文件里，最旧的会被删除 2 个，剩下最新 2 个文件
    cleanup_old_backups(backup_dir=backup_dir, keep_daily=10, keep_weekly=0, max_size_gb=0.013)
    
    files = sorted([f for f in os.listdir(backup_dir) if f.startswith("db_") and f.endswith(".sqlite")])
    assert len(files) == 2
    # 验证留下的是最新两个文件
    assert os.path.exists(files_to_create[2])
    assert os.path.exists(files_to_create[3])

def test_scenario7_cli_shell_validation(temp_env):
    """场景 7：验证 CLI 工具在不同命令行参数下的 exit code。"""
    db_path, backup_dir = temp_env
    
    # 1. 测试 --dry-run
    test_args = [
        "backup_database.py",
        "--db-path", db_path,
        "--backup-dir", backup_dir,
        "--dry-run"
    ]
    with patch("sys.argv", test_args):
        rc = cli_main()
        assert rc == 0
        
    # 2. 测试正常执行备份
    test_args = [
        "backup_database.py",
        "--db-path", db_path,
        "--backup-dir", backup_dir
    ]
    with patch("sys.argv", test_args):
        rc = cli_main()
        assert rc == 0
        
    # 3. 再次执行备份（因为无变化，所以跳过，应当返回 2）
    with patch("sys.argv", test_args):
        rc = cli_main()
        assert rc == 2
        
    # 4. 测试源 db 不存在（应当失败，返回 1）
    test_args = [
        "backup_database.py",
        "--db-path", "/non_existent_file.sqlite",
        "--backup-dir", backup_dir
    ]
    with patch("sys.argv", test_args):
        rc = cli_main()
        assert rc == 1
