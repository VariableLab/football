# -*- coding: utf-8 -*-
"""
sync_wc_completed_results.py — 2026 世界杯完赛结果补录与数据纠错脚本
"""

import os
import sys
import sqlite3
from datetime import datetime, timezone

# 配置 Python 路径以包含 backend 及子目录
_cwd = os.getcwd()
_root = os.path.join(_cwd, 'backend')
if not os.path.exists(_root):
    _root = _cwd

for d in ["api", "core", "features", "ingestion", "database", "strategy", "monitor", "utils", "api/routers"]:
    sys.path.append(os.path.join(_root, d))
sys.path.append(_root)

def main():
    db_path = os.path.join(_root, 'database.sqlite')
    print(f"正在连接数据库: {db_path}")
    
    if not os.path.exists(db_path):
        print(f"[错误] 数据库文件不存在: {db_path}")
        sys.exit(1)
        
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # 待补录和修正的完赛结果数据
    # 格式: (actual_home_goals, actual_away_goals, actual_outcome, status, match_code)
    updates = [
        # 1. 修正 6月11日 加拿大 vs 波黑（原库内错误录入为 4-1）
        (1, 1, 'draw', 'FINISHED', 'WC2026-OPEN-2'),
        # 2. 补录 6月9日 中国 vs 泰国（原库内状态为 LIVE）
        (0, 0, 'draw', 'FINISHED', 'JC-20260609-中国-泰国'),
        # 3. 补录 6月13-14日世界杯小组赛结果（原库内状态为 SCHEDULED）
        (1, 1, 'draw', 'FINISHED', 'JC-20260614-卡塔尔-瑞士'),
        (1, 1, 'draw', 'FINISHED', 'JC-20260614-巴西-摩洛哥'),
        (0, 1, 'away', 'FINISHED', 'JC-20260614-海地-苏格兰'),
        (2, 0, 'home', 'FINISHED', 'JC-20260614-澳大利-土耳其'),
        # 4. 补录 6月15日世界杯完赛结果 (打完但卡在 SCHEDULED 或 LIVE)
        (7, 1, 'home', 'FINISHED', 'JC-20260615-德国-库拉索'),
        (2, 2, 'draw', 'FINISHED', 'JC-20260615-荷兰-日本'),
        (1, 0, 'home', 'FINISHED', 'JC-20260615-科特迪-厄瓜多'),
        (5, 1, 'home', 'FINISHED', 'JC-20260615-瑞典-突尼斯')
    ]
    
    print("\n=== 开始执行数据库数据更新 ===")
    updated_count = 0
    for h_g, a_g, outcome, status, code in updates:
        cursor.execute("""
            UPDATE matches 
            SET actual_home_goals = ?, 
                actual_away_goals = ?, 
                actual_outcome = ?, 
                status = ?
            WHERE match_code = ?
        """, (h_g, a_g, outcome, status, code))
        
        if cursor.rowcount > 0:
            print(f"  [成功] 比赛 {code} 更新为 比分 {h_g}:{a_g}，结果 {outcome}，状态 {status}")
            updated_count += 1
        else:
            # 检查是否因为已经在库内以相同的值存在
            cursor.execute("SELECT id FROM matches WHERE match_code = ?", (code,))
            row = cursor.fetchone()
            if row:
                print(f"  [跳过] 比赛 {code} 已存在，未发生数据更改。")
            else:
                print(f"  [警告] 未找到 match_code 为 '{code}' 的比赛记录。")
                
    conn.commit()
    conn.close()
    print(f"=== 数据库更新完成 (共更新 {updated_count} 场) ===\n")
    
    # ─── 触发 ModelAuditor 审计重算 ───
    print("=== 开始运行 ModelAuditor 触发过去 7 天赛果复盘审计 ===")
    try:
        from model_audit import ModelAuditor
        auditor = ModelAuditor()
        # 审计过去 7 天的完赛预测 (包含 6月9日 到 6月15日)
        report = auditor.run_daily_audit(days_back=7)
        if report:
            print(f"  [审计完成] 生成的复盘日期: {report.date}")
            print(f"  [审计结果] 过去 7 天完赛总场次: {report.total}")
            print(f"  [审计结果] 预测方向准确率: {report.direction_accuracy:.2%}")
            print(f"  [审计结果] Brier Score: {report.brier_score:.4f}")
            print(f"  [审计结果] RPS Score: {report.rps_score:.4f}")
            print(f"  [提示] 详细报告已保存至 data/model_audit/audit_{report.date}.json")
        else:
            print("  [提示] 未发现符合复盘条件的已预测已完赛比赛。")
    except Exception as e:
        print(f"  [错误] 运行 ModelAuditor 时抛出异常: {e}")
        import traceback
        traceback.print_exc()

if __name__ == '__main__':
    main()
