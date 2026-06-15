
import sqlite3
import os

db_path = "backend/database.sqlite"

if not os.path.exists(db_path):
    print(f"❌ 数据库文件不存在: {db_path}")
    exit(1)

try:
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # 检查字段是否已存在
    cursor.execute("PRAGMA table_info(matches)")
    columns = [row[1] for row in cursor.fetchall()]
    
    if "poster_url" not in columns:
        print("正在为 matches 表增加 poster_url 字段...")
        cursor.execute("ALTER TABLE matches ADD COLUMN poster_url VARCHAR(500)")
        conn.commit()
        print("✅ 字段增加成功！")
    else:
        print("ℹ️ 字段 poster_url 已存在，无需修改。")
        
    conn.close()
except Exception as e:
    print(f"❌ 迁移失败: {e}")
