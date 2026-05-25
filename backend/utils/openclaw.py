#!/usr/bin/env python3
"""
OpenClaw — WC Analytics 管理脚本
用法: python openclaw.py <command> [options]

Commands:
  dashboard              查看系统仪表盘
  teams                  列出所有球队
  matches                列出所有比赛
  create-match           创建新比赛
  result <match_id>      录入比赛结果
  odds <match_id>        更新比赛赔率
  predict <match_id>     为比赛生成预测
  validate [match_id]    运行赛后验证
  licenses               列出所有 License
  gen-licenses           批量生成 License
  users                  列出所有用户
  audit                  查看审计日志
  backup                 备份数据库
  status                 检查系统状态
  help                   显示帮助

环境变量:
  OPENCLAW_URL      服务器地址 (默认: http://localhost:8000)
  OPENCLAW_KEY      Admin API Key (默认: 从 .env 读取)
"""

from __future__ import annotations

import os
import sys
import json
import argparse
from datetime import datetime
from pathlib import Path
from typing import Optional

import httpx

# ─── 配置 ─────────────────────────────────────────
DEFAULT_URL = os.getenv("OPENCLAW_URL", "http://localhost:8000")
ENV_PATH = Path(__file__).parent / ".env"


def _load_key_from_env() -> str:
    """从 .env 文件读取 ADMIN_API_KEY"""
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text().splitlines():
            if line.startswith("ADMIN_API_KEY="):
                return line.split("=", 1)[1].strip()
    return os.getenv("OPENCLAW_KEY", "")


API_KEY = _load_key_from_env()
if not API_KEY:
    print("❌ 错误: 未找到 ADMIN_API_KEY。请设置 OPENCLAW_KEY 环境变量或在 .env 中配置")
    sys.exit(1)

HEADERS = {
    "X-Api-Key": API_KEY,
    "Content-Type": "application/json",
    "Accept": "application/json",
}

client = httpx.Client(base_url=DEFAULT_URL, headers=HEADERS, timeout=30.0)


# ─── 工具函数 ─────────────────────────────────────────

def _get(endpoint: str, params: Optional[dict] = None) -> dict:
    r = client.get(endpoint, params=params)
    r.raise_for_status()
    return r.json()


def _post(endpoint: str, json_data: Optional[dict] = None) -> dict:
    r = client.post(endpoint, json=json_data)
    r.raise_for_status()
    return r.json()


def _patch(endpoint: str, json_data: Optional[dict] = None) -> dict:
    r = client.patch(endpoint, json=json_data)
    r.raise_for_status()
    return r.json()


def _print_json(data: dict):
    print(json.dumps(data, indent=2, ensure_ascii=False, default=str))


def _confirm(prompt: str) -> bool:
    return input(f"{prompt} [y/N]: ").strip().lower() == "y"


# ─── 命令实现 ─────────────────────────────────────────

def cmd_dashboard():
    """查看系统仪表盘"""
    data = _get("/api/admin/dashboard")
    print("\n📊 系统仪表盘")
    print("─" * 40)
    print(f"  总比赛数:     {data.get('total_matches', 0)}")
    print(f"  已结束:       {data.get('finished_matches', 0)}")
    print(f"  总预测数:     {data.get('total_predictions', 0)}")
    print(f"  预测准确率:   {data.get('prediction_accuracy', 0):.1%}")
    print(f"  总用户数:     {data.get('total_users', 0)}")
    print(f"  付费用户:     {data.get('paid_users', 0)}")
    print()


def cmd_teams():
    """列出所有球队"""
    data = _get("/api/admin/teams")
    print(f"\n⚽ 球队列表 ({len(data)} 支)")
    print("─" * 60)
    print(f"{'ID':>4} {'Code':>6} {'名称':<20} {'ELO':>6} {'FIFA':>6}")
    print("─" * 60)
    for t in data:
        print(f"{t.get('id', 0):>4} {t.get('code', '-'):>6} {t.get('name', '-'):<20} "
              f"{t.get('elo', 0):>6} {t.get('fifa_rank', '-'):>6}")
    print()


def cmd_matches(args):
    """列出所有比赛"""
    params = {}
    if args.status:
        params["status"] = args.status
    if args.group:
        params["group"] = args.group
    if args.stage:
        params["stage"] = args.stage

    data = _get("/api/admin/matches", params=params)
    print(f"\n🏟️ 比赛列表 ({len(data)} 场)")
    print("─" * 90)
    print(f"{'ID':>4} {'对阵':<35} {'阶段':>8} {'状态':>8} {'开球时间':>20}")
    print("─" * 90)
    for m in data:
        home = m.get("home_team", {}).get("name", "?")[:14]
        away = m.get("away_team", {}).get("name", "?")[:14]
        vs = f"{home} vs {away}"
        stage = m.get("stage", "-")
        status = m.get("status", "-")
        kickoff = m.get("kickoff_at", "-")
        if isinstance(kickoff, str) and len(kickoff) > 16:
            kickoff = kickoff[:16]
        print(f"{m.get('id', 0):>4} {vs:<35} {stage:>8} {status:>8} {kickoff:>20}")
    print()


def cmd_create_match(args):
    """创建新比赛"""
    payload = {
        "match_code": args.code,
        "home_team_id": args.home_id,
        "away_team_id": args.away_id,
        "stage": args.stage,
        "group": args.group or None,
        "kickoff_at": args.kickoff,
        "competition": args.competition or "world_cup",
        "odds_home": args.odds_home or 0.0,
        "odds_draw": args.odds_draw or 0.0,
        "odds_away": args.odds_away or 0.0,
    }
    data = _post("/api/admin/matches", payload)
    print(f"\n✅ 比赛创建成功")
    _print_json(data)


def cmd_result(args):
    """录入比赛结果"""
    payload = {
        "actual_home_goals": args.home_goals,
        "actual_away_goals": args.away_goals,
    }
    data = _patch(f"/api/admin/matches/{args.match_id}/result", payload)
    print(f"\n✅ 赛果已更新")
    _print_json(data)


def cmd_odds(args):
    """更新比赛赔率"""
    data = _patch(
        f"/api/admin/matches/{args.match_id}/odds",
        {"odds_home": args.home, "odds_draw": args.draw, "odds_away": args.away}
    )
    print(f"\n✅ 赔率已更新")
    _print_json(data)


def cmd_predict(args):
    """为比赛生成预测（调用公共策略接口）"""
    data = _get(f"/api/matches/{args.match_id}/strategy")
    print(f"\n🔮 比赛预测与策略")
    print("─" * 50)
    print(f"比赛ID: {data.get('match_id')}")
    print(f"状态:   {data.get('status')}")
    print(f"信心:   {data.get('confidence')}")
    print("\n📊 预测概率:")
    for p in data.get("predictions", []):
        print(f"  {p['play_type']}: {p['probabilities']}")
    print("\n🎯 投注策略:")
    for s in data.get("strategies", []):
        print(f"  {s.get('name', '-')} → {s.get('pick', '-')} "
              f"(EV: {s.get('expected_value', 0):.2f}, 仓位: {s.get('stake_pct', 0):.1%})")
    print()


def cmd_validate(args):
    """运行赛后验证"""
    if args.match_id:
        data = _get(f"/api/admin/validation/matches/{args.match_id}")
        print(f"\n✅ 单场比赛验证结果")
    else:
        params = {}
        if args.match_type:
            params["match_type"] = args.match_type
        data = _get("/api/admin/validation", params=params)
        print(f"\n✅ 批量验证报告")
    _print_json(data)


def cmd_licenses():
    """列出所有 License"""
    data = _get("/api/admin/licenses")
    print(f"\n🔑 License 列表 ({len(data)} 个)")
    print("─" * 70)
    print(f"{'ID':>4} {'Key':<30} {'类型':>10} {'已使用':>8} {'使用者':>10}")
    print("─" * 70)
    for lic in data:
        used = "✅" if lic.get("is_used") else "❌"
        user = lic.get("used_by_user_id") or "-"
        print(f"{lic.get('id', 0):>4} {lic.get('key', '-'):<30} "
              f"{lic.get('license_type', '-'):>10} {used:>8} {user:>10}")
    print()


def cmd_gen_licenses(args):
    """批量生成 License"""
    payload = {
        "license_type": args.type,
        "count": args.count,
        "match_id": args.match_id,
    }
    data = _post("/api/admin/licenses/generate", payload)
    print(f"\n✅ 已生成 {data.get('generated', 0)} 个 License")
    for k in data.get("keys", []):
        print(f"  {k['key']} ({k['type']})")
    print()


def cmd_users():
    """列出所有用户"""
    # 用户列表没有 admin 接口，通过公共接口或数据库查询
    # 这里使用已有的 /api/users/me 无法获取全部，需要扩展
    # 暂时提示
    print("\n⚠️ 当前 API 没有 /api/admin/users 端点")
    print("   如需查看全部用户，请直接查询数据库:")
    print("   sqlite3 database.sqlite 'SELECT id, username, is_paid, license_type FROM users;'")
    print()


def cmd_audit(args):
    """查看审计日志"""
    params = {"limit": args.limit}
    if args.match_id:
        params["match_id"] = args.match_id
    if args.data_type:
        params["data_type"] = args.data_type
    data = _get("/api/admin/audit-logs", params=params)
    print(f"\n📋 审计日志 ({len(data)} 条)")
    print("─" * 90)
    print(f"{'ID':>4} {'时间':<20} {'类型':>12} {'比赛ID':>6} {'来源':>12}")
    print("─" * 90)
    for log in data:
        ts = log.get("ingest_timestamp", "-")
        if isinstance(ts, str) and len(ts) > 19:
            ts = ts[:19]
        print(f"{log.get('id', 0):>4} {ts:<20} {log.get('data_type', '-'):>12} "
              f"{log.get('match_id') or '-':>6} {log.get('source', '-'):>12}")
    print()


def cmd_backup():
    """备份数据库"""
    db_path = Path(__file__).parent / "database.sqlite"
    if not db_path.exists():
        print("❌ 数据库文件不存在")
        return

    backup_dir = Path(__file__).parent / "backup"
    backup_dir.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = backup_dir / f"database_backup_{timestamp}.sqlite"

    import shutil
    shutil.copy2(db_path, backup_path)
    print(f"\n💾 数据库已备份")
    print(f"   源文件: {db_path}")
    print(f"   备份:   {backup_path}")
    print()


def cmd_status():
    """检查系统状态"""
    try:
        # 检查 API 是否可达
        r = client.get("/api/admin/dashboard")
        api_ok = r.status_code == 200
    except Exception:
        api_ok = False

    db_path = Path(__file__).parent / "database.sqlite"
    db_size = db_path.stat().st_size if db_path.exists() else 0

    print("\n🔍 系统状态检查")
    print("─" * 40)
    print(f"  API 服务:     {'✅ 正常' if api_ok else '❌ 无法连接'}")
    print(f"  服务器地址:   {DEFAULT_URL}")
    print(f"  数据库:       {'✅ 存在' if db_path.exists() else '❌ 缺失'}")
    print(f"  数据库大小:   {db_size / 1024 / 1024:.2f} MB")
    print(f"  当前时间:     {datetime.now().isoformat()}")
    print()


# ─── CLI 入口 ─────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        prog="openclaw",
        description="WC Analytics 管理脚本",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    subparsers = parser.add_subparsers(dest="command", help="可用命令")

    # dashboard
    subparsers.add_parser("dashboard", help="查看系统仪表盘")

    # teams
    subparsers.add_parser("teams", help="列出所有球队")

    # matches
    p_matches = subparsers.add_parser("matches", help="列出所有比赛")
    p_matches.add_argument("--status", help="按状态筛选")
    p_matches.add_argument("--group", help="按小组筛选")
    p_matches.add_argument("--stage", help="按阶段筛选")

    # create-match
    p_create = subparsers.add_parser("create-match", help="创建新比赛")
    p_create.add_argument("--code", required=True, help="比赛代码 (如 WC2026-A1)")
    p_create.add_argument("--home-id", type=int, required=True, help="主队 ID")
    p_create.add_argument("--away-id", type=int, required=True, help="客队 ID")
    p_create.add_argument("--stage", required=True, help="阶段 (group/R16/QF/SF/F)")
    p_create.add_argument("--group", help="小组 (A/B/C...)")
    p_create.add_argument("--kickoff", required=True, help="开球时间 (ISO 格式)")
    p_create.add_argument("--competition", default="world_cup", help="赛事类型")
    p_create.add_argument("--odds-home", type=float, help="主胜赔率")
    p_create.add_argument("--odds-draw", type=float, help="平局赔率")
    p_create.add_argument("--odds-away", type=float, help="客胜赔率")

    # result
    p_result = subparsers.add_parser("result", help="录入比赛结果")
    p_result.add_argument("match_id", type=int, help="比赛 ID")
    p_result.add_argument("--home-goals", type=int, required=True, help="主队进球")
    p_result.add_argument("--away-goals", type=int, required=True, help="客队进球")

    # odds
    p_odds = subparsers.add_parser("odds", help="更新比赛赔率")
    p_odds.add_argument("match_id", type=int, help="比赛 ID")
    p_odds.add_argument("--home", type=float, required=True, help="主胜赔率")
    p_odds.add_argument("--draw", type=float, required=True, help="平局赔率")
    p_odds.add_argument("--away", type=float, required=True, help="客胜赔率")

    # predict
    p_predict = subparsers.add_parser("predict", help="查看比赛预测")
    p_predict.add_argument("match_id", type=int, help="比赛 ID")

    # validate
    p_validate = subparsers.add_parser("validate", help="运行赛后验证")
    p_validate.add_argument("match_id", type=int, nargs="?", help="比赛 ID (可选)")
    p_validate.add_argument("--match-type", help="筛选类型 (world_cup/friendly/warm_up)")

    # licenses
    subparsers.add_parser("licenses", help="列出所有 License")

    # gen-licenses
    p_gen = subparsers.add_parser("gen-licenses", help="批量生成 License")
    p_gen.add_argument("--type", choices=["match", "tournament"], required=True, help="类型")
    p_gen.add_argument("--count", type=int, default=1, help="数量")
    p_gen.add_argument("--match-id", type=int, help="关联比赛 ID (match 类型必填)")

    # users
    subparsers.add_parser("users", help="列出所有用户")

    # audit
    p_audit = subparsers.add_parser("audit", help="查看审计日志")
    p_audit.add_argument("--match-id", type=int, help="按比赛筛选")
    p_audit.add_argument("--data-type", help="按数据类型筛选")
    p_audit.add_argument("--limit", type=int, default=50, help="返回条数")

    # backup
    subparsers.add_parser("backup", help="备份数据库")

    # status
    subparsers.add_parser("status", help="检查系统状态")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(0)

    commands = {
        "dashboard": cmd_dashboard,
        "teams": cmd_teams,
        "matches": lambda: cmd_matches(args),
        "create-match": lambda: cmd_create_match(args),
        "result": lambda: cmd_result(args),
        "odds": lambda: cmd_odds(args),
        "predict": lambda: cmd_predict(args),
        "validate": lambda: cmd_validate(args),
        "licenses": cmd_licenses,
        "gen-licenses": lambda: cmd_gen_licenses(args),
        "users": cmd_users,
        "audit": lambda: cmd_audit(args),
        "backup": cmd_backup,
        "status": cmd_status,
    }

    try:
        commands[args.command]()
    except httpx.HTTPStatusError as e:
        print(f"\n❌ API 错误: {e.response.status_code}")
        try:
            detail = e.response.json().get("detail", e.response.text)
            print(f"   详情: {detail}")
        except Exception:
            print(f"   响应: {e.response.text[:200]}")
        sys.exit(1)
    except httpx.ConnectError:
        print(f"\n❌ 无法连接到服务器: {DEFAULT_URL}")
        print("   请检查服务是否运行，或设置 OPENCLAW_URL 环境变量")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ 错误: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
