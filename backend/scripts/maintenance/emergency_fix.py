#!/usr/bin/env python3
"""
紧急修复脚本 — 一次性诊断 + 自动修复 + 报告
用法: python emergency_fix.py [--fix] [--verbose]

--fix        尝试自动修复可修复的问题
--verbose    输出详细诊断日志
"""
import argparse
import json
import os
import sys
import sqlite3
import subprocess
from datetime import datetime, timezone
from typing import Dict, List, Optional

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(PROJECT_ROOT, "database.sqlite")
VENV_PYTHON = os.path.join(PROJECT_ROOT, "venv", "bin", "python")


def log(msg: str, status: str = "INFO") -> None:
    icon = {"PASS": "✅", "FAIL": "❌", "WARN": "⚠️", "INFO": "ℹ️", "FIX": "🔧", "SKIP": "⏭️"}
    print(f"  {icon.get(status, '•')} [{status}] {msg}")


def check_ssl() -> Dict:
    ips = ["https://live.zgzcw.com", "https://google.com"]
    for url in ips:
        try:
            import httpx
            r = httpx.get(url, timeout=10, follow_redirects=True)
            if r.status_code < 400:
                return {"status": "pass", "detail": f"{url} reachable ({r.status_code})"}
        except Exception as e:
            return {"status": "fail", "detail": f"{url} failed: {e}"}
    return {"status": "fail", "detail": "all endpoints unreachable"}


def check_zgzcw_sync(db_path: str = DB_PATH) -> Dict:
    try:
        from ingestion.zgzcw_jc_sync import sync_jc_matches
        result = sync_jc_matches(db_path)
        if not isinstance(result, dict):
            return {"status": "fail", "detail": f"unexpected return: {result}"}
        errs = result.get("errors", 0)
        matches = result.get("matches", 0)
        linked = result.get("issues_linked", 0)
        if errs > 0 and matches == 0:
            return {"status": "fail", "detail": f"sync errors: {errs}, matches={matches}"}
        return {"status": "pass", "detail": f"synced {matches} matches, {linked} linked"}
    except Exception as e:
        return {"status": "fail", "detail": f"sync exception: {e}"}


def check_db_integrity(db_path: str = DB_PATH) -> Dict:
    if not os.path.exists(db_path):
        return {"status": "fail", "detail": f"db not found at {db_path}"}
    try:
        conn = sqlite3.connect(db_path)
        cur = conn.execute("PRAGMA integrity_check")
        if cur.fetchone()[0] != "ok":
            conn.close()
            return {"status": "fail", "detail": "integrity check failed"}
        conn.close()
        return {"status": "pass", "detail": "integrity ok"}
    except Exception as e:
        return {"status": "fail", "detail": f"db error: {e}"}


def check_tables(db_path: str = DB_PATH) -> Dict:
    required = ["matches", "teams", "predictions", "jingcai_issues", "jingcai_issue_matches", "odds_history"]
    try:
        conn = sqlite3.connect(db_path)
        cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        existing = {r[0] for r in cur.fetchall()}
        conn.close()
        missing = [t for t in required if t not in existing]
        if missing:
            return {"status": "fail", "detail": f"missing tables: {missing}"}
        return {"status": "pass", "detail": f"all {len(required)} required tables exist"}
    except Exception as e:
        return {"status": "fail", "detail": f"table check error: {e}"}


def check_data_stats(db_path: str = DB_PATH) -> Dict:
    try:
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM matches")
        matches = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM teams")
        teams = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM predictions")
        predictions = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM jingcai_issues")
        issues = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM jingcai_issue_matches")
        issue_links = cur.fetchone()[0]
        conn.close()
        detail = f"{matches} matches, {teams} teams, {predictions} preds, {issues} issues, {issue_links} links"
        issues_ok = issues > 0 and issue_links > 0
        return {"status": "pass" if issues_ok else "warn", "detail": detail}
    except Exception as e:
        return {"status": "fail", "detail": f"stats error: {e}"}


def check_lr_weights() -> Dict:
    weights_dir = os.path.join(PROJECT_ROOT, "data", "weights", "lr")
    if not os.path.exists(weights_dir):
        return {"status": "fail", "detail": f"weights dir not found: {weights_dir}"}
    import glob
    files = sorted(glob.glob(os.path.join(weights_dir, "global_*.json")))
    if not files:
        return {"status": "fail", "detail": "no global_*.json weight files found"}
    try:
        with open(files[-1]) as f:
            data = json.load(f)
        accuracy = data.get("accuracy", data.get("metrics", {}).get("accuracy", "unknown"))
        return {"status": "pass", "detail": f"{len(files)} files, latest acc={accuracy}"}
    except Exception as e:
        return {"status": "warn", "detail": f"files exist ({len(files)}) but read error: {e}"}


def check_residual_nn() -> Dict:
    model_path = os.path.join(PROJECT_ROOT, "data", "bet_nn", "residual_net.pt")
    if os.path.exists(model_path):
        size_kb = os.path.getsize(model_path) / 1024
        return {"status": "pass", "detail": f"model exists ({size_kb:.0f} KB)"}
    return {"status": "fail", "detail": "residual_net.pt not found"}


def check_prediction_coverage(db_path: str = DB_PATH) -> Dict:
    try:
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        now = datetime.now(timezone.utc).isoformat()
        cur.execute("""
            SELECT COUNT(*) FROM matches
            WHERE status IN ('SCHEDULED', 'UPCOMING')
              AND kickoff_at > ?
        """, (now,))
        total = cur.fetchone()[0]
        cur.execute("""
            SELECT COUNT(DISTINCT m.id) FROM matches m
            LEFT JOIN predictions p ON p.match_id = m.id
            WHERE m.status IN ('SCHEDULED', 'UPCOMING')
              AND m.kickoff_at > ?
              AND p.id IS NULL
        """, (now,))
        missing = cur.fetchone()[0]
        conn.close()
        if total == 0:
            return {"status": "pass", "detail": "no upcoming matches"}
        pct = ((total - missing) / total) * 100
        if pct >= 80:
            return {"status": "pass", "detail": f"{pct:.0f}% coverage ({total-missing}/{total})"}
        return {"status": "warn", "detail": f"{pct:.0f}% coverage ({total-missing}/{total})"}
    except Exception as e:
        return {"status": "fail", "detail": f"coverage error: {e}"}


def check_ssl_bundle() -> Dict:
    try:
        import certifi
        cafile = certifi.where()
        if os.path.exists(cafile):
            return {"status": "pass", "detail": f"certifi CA bundle: {cafile}"}
        return {"status": "warn", "detail": "certifi installed but CA file not found"}
    except ImportError:
        return {"status": "warn", "detail": "certifi not installed"}


def try_fix_zgzcw() -> bool:
    try:
        from ingestion.zgzcw_jc_sync import sync_jc_matches
        result = sync_jc_matches(DB_PATH)
        return result.get("errors", 100) == 0
    except Exception:
        return False


def try_fix_ssl() -> bool:
    try:
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "certifi>=2024.0.0"],
            capture_output=True, cwd=PROJECT_ROOT,
        )
        return True
    except Exception:
        return False


CHECKS = [
    ("ssl_connectivity", "SSL/网络连通性", check_ssl),
    ("ssl_bundle", "SSL CA证书", check_ssl_bundle),
    ("db_integrity", "数据库完整性", check_db_integrity),
    ("db_tables", "数据库表结构", check_tables),
    ("data_stats", "数据统计", check_data_stats),
    ("zgzcw_sync", "竞彩同步(zgzcw)", check_zgzcw_sync),
    ("lr_weights", "LR融合权重", check_lr_weights),
    ("residual_nn", "残差NN模型", check_residual_nn),
    ("prediction_coverage", "预测覆盖率", check_prediction_coverage),
]

FIXES = {
    "ssl_connectivity": ("pip install certifi", try_fix_ssl),
    "ssl_bundle": ("pip install certifi", try_fix_ssl),
    "zgzcw_sync": ("重新运行竞彩同步", try_fix_zgzcw),
}


def run_diagnostics(fix: bool = False, verbose: bool = False) -> Dict:
    print(f"\n{'='*60}")
    print(f"  🔍 系统诊断报告")
    print(f"  {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print(f"  数据库: {DB_PATH}")
    print(f"{'='*60}\n")

    results = {}
    all_pass = True

    for key, name, fn in CHECKS:
        try:
            result = fn()
        except Exception as e:
            result = {"status": "fail", "detail": f"unexpected error: {e}"}

        results[key] = result
        if result["status"] != "pass":
            all_pass = False

        log(f"{name}: {result['detail']}", result["status"].upper())

        if fix and result["status"] == "fail" and key in FIXES:
            desc, fix_fn = FIXES[key]
            log(f"尝试自动修复: {desc}", "FIX")
            try:
                ok = fix_fn()
                if ok:
                    log(f"修复成功，重新验证...", "INFO")
                    retry = fn()
                    if retry["status"] == "pass":
                        log(f"验证通过: {retry['detail']}", "PASS")
                        results[key] = retry
                    else:
                        log(f"验证仍失败: {retry['detail']}", "FAIL")
                else:
                    log(f"修复执行失败", "FAIL")
            except Exception as e:
                log(f"修复异常: {e}", "FAIL")

    print(f"\n{'='*60}")
    overall = "PASS" if all_pass else "ISSUES_FOUND"
    print(f"  总体: {'✅ 所有检查通过' if all_pass else '❌ 发现问题'}")
    print(f"{'='*60}\n")

    if not all_pass:
        print("  需要人工处理的项:")
        for key, name, fn in CHECKS:
            r = results.get(key, {})
            if r.get("status") != "pass":
                print(f"    [{r.get('status','?')}] {name}: {r.get('detail','?')}")
        print()

    results["_meta"] = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "overall": "pass" if all_pass else "issues",
        "db_path": DB_PATH,
        "fix_mode": fix,
    }
    return results


def main():
    parser = argparse.ArgumentParser(description="竞彩预测系统 — 紧急诊断修复工具")
    parser.add_argument("--fix", action="store_true", help="尝试自动修复可修复的问题")
    parser.add_argument("--verbose", action="store_true", help="输出详细日志")
    parser.add_argument("--json", action="store_true", help="以JSON格式输出结果")
    parser.add_argument("--cron", action="store_true", help="静默模式：仅JSON输出，检测到问题时返回非0退出码")
    args = parser.parse_args()

    if args.cron:
        args.json = True

    results = run_diagnostics(fix=args.fix, verbose=args.verbose)

    if args.json or args.cron:
        print(json.dumps(results, indent=2 if not args.cron else None, ensure_ascii=False))

    if args.cron and results.get("_meta", {}).get("overall") != "pass":
        sys.exit(1)


if __name__ == "__main__":
    main()
