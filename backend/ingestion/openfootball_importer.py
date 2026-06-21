"""
openfootball/football.json 数据导入器
https://github.com/openfootball/football.json — 免费公开足球数据

功能:
1. 历史回填 — 16赛季5大联赛历史比赛+比分 (2010-11 → 2024-25)
2. 当赛季结果同步 — 免费、无API Key，替代 football-data.org
3. 半场比分 — score.ht 数据用于半全场预测校准

用法:
  # 方式1: 在线模式 (需要能访问 GitHub)
  python3 openfootball_importer.py --backfill
  python3 openfootball_importer.py --sync-results
  python3 openfootball_importer.py --full

  # 方式2: 本地模式 (先手动克隆仓库)
  git clone https://github.com/openfootball/football.json.git /tmp/openfootball-data
  python3 openfootball_importer.py --backfill --local /tmp/openfootball-data
  python3 openfootball_importer.py --sync-results --local /tmp/openfootball-data

  # Dry run (只看不写)
  python3 openfootball_importer.py --backfill --dry-run
"""

import argparse
import json
import ssl
import sqlite3
import sys
import time
import urllib.request
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path

DB_PATH = Path(__file__).parent / "database.sqlite"
BASE_URL = "https://raw.githubusercontent.com/openfootball/football.json/master"

LEAGUE_MAP = {
    "en.1": "EPL", "en.2": "Championship", "en.3": "League1", "en.4": "League2",
    "de.1": "Bundesliga", "de.2": "Bundesliga2", "de.3": "Bundesliga3",
    "es.1": "LaLiga", "es.2": "LaLiga2",
    "it.1": "SerieA", "it.2": "SerieB",
    "fr.1": "Ligue1", "fr.2": "Ligue2",
    "at.1": "Austrian1", "at.2": "Austrian2",
    "nl.1": "Eredivisie", "pt.1": "Portugal1",
    "uefa.cl": "UCL", "uefa.el": "UEL",
}

BACKFILL_SEASONS = [f"{y}-{y+1-2000}" for y in range(2010, 2026)]
BACKFILL_LEAGUES = ["en.1", "de.1", "es.1", "it.1", "fr.1"]

SYNC_SEASON = "2025-26"
SYNC_LEAGUES = ["en.1", "de.1", "es.1", "it.1", "fr.1",
                "en.2", "de.2", "es.2", "it.2", "fr.2"]

MATCH_THRESHOLD = 0.55

_ctx = ssl.create_default_context()


# ─── 数据获取 ───

def fetch_json_online(url: str, retries: int = 3) -> dict | None:
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "WC-Analytics/1.0"})
            with urllib.request.urlopen(req, timeout=30, context=_ctx) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
            else:
                print(f"    [WARN] Failed: {url} — {e}")
                return None


def fetch_json_local(local_root: Path, season: str, league_code: str) -> dict | None:
    path = local_root / season / f"{league_code}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"    [WARN] Read failed: {path} — {e}")
        return None


# ─── 队名匹配 ───

def normalize_team_name(name: str) -> str:
    from data_cleaner import resolve_team_name
    # 1. 尝试使用中心别名库
    norm = resolve_team_name(name)
    if norm != name:
        return norm

    # 2. 回退到基础正则清理 (保持向后兼容)
    s = name.lower().strip()
    for suffix in [" fc", " cf", " sc", " afc", " ac", " bfc", " sv", " e.v",
                    " 1846", " 1848", " 1909", " 1907", " 1910", " 04",
                    " srl", " spa", " inc", " a/s"]:
        s = s.replace(suffix, "")
    for prefix in ["1. fc ", "1 fc ", "fc ", "club atletico ", "club ",
                    "deportivo ", "rayo vallecano de "]:
        if s.startswith(prefix):
            s = s.replace(prefix, "", 1)
    # 清理前导数字+点 (如 "1." 开头)
    import re
    s = re.sub(r"^\d+\.\s*", "", s)
    
    return s.strip()



class TeamMatcher:
    def __init__(self, db_path: str, conn: sqlite3.Connection | None = None):
        self._db_path = db_path
        self._index = {}
        self._teams = []
        self._next_id = 0
        self._code_set = set()
        own_conn = conn is None
        if own_conn:
            conn = sqlite3.connect(db_path)
        self._own_conn = own_conn
        self._conn = conn
        cur = conn.cursor()
        cur.execute("SELECT id, name, name_en, code, elo FROM teams ORDER BY id")
        for row in cur.fetchall():
            team = {"id": row[0], "name": row[1], "name_en": row[2], "code": row[3], "elo": row[4]}
            self._teams.append(team)
            for n in [row[1], row[2]]:
                if n:
                    key = normalize_team_name(n)
                    self._index[key] = team
            if row[3]:
                self._code_set.add(row[3])
            self._next_id = max(self._next_id, row[0] + 1)
        if own_conn:
            conn.close()

    def _generate_code(self, name_en: str) -> str:
        """为球队生成唯一 code"""
        words = name_en.replace("&", "").replace("-", " ").split()
        if len(words) >= 2:
            code = (words[0][:3] + words[1][:2]).upper()
        else:
            code = words[0][:5].upper()
        # 确保唯一
        base = code
        i = 2
        while code in self._code_set:
            code = f"{base}{i}"
            i += 1
        self._code_set.add(code)
        return code

    def _create_team(self, of_name: str) -> dict:
        """匹配不到时自动创建新球队"""
        name_en = of_name.replace(" FC", "").replace(" AFC", "").replace(" SC", "").strip()
        code = self._generate_code(name_en)

        team = {
            "id": self._next_id,
            "name": name_en,
            "name_en": name_en,
            "code": code,
            "elo": 1500,  # 默认 Elo
        }
        self._next_id += 1
        self._teams.append(team)
        norm = normalize_team_name(of_name)
        self._index[norm] = team

        if self._own_conn:
            conn = sqlite3.connect(self._db_path)
        else:
            conn = self._conn
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO teams (id, name, name_en, code, elo, avg_xg, avg_xga)
            VALUES (?, ?, ?, ?, ?, 1.2, 1.2)
            """, (team["id"], team["name"], team["name_en"], team["code"], team["elo"]))
        conn.commit()
        if self._own_conn:
            conn.close()

        print(f"    [NEW TEAM] {of_name} → id={team['id']} code={code}")
        return team

    def match(self, of_name: str, auto_create: bool = True) -> dict | None:
        norm = normalize_team_name(of_name)

        # 0) 规范名 DB 查找 (data_cleaner)
        from data_cleaner import resolve_team_name
        canonical = resolve_team_name(of_name)
        for key, team in self._index.items():
            if key == canonical.lower() or key == canonical:
                return team

        # 1) 精确匹配
        if norm in self._index:
            return self._index[norm]

        # 2) 子串匹配 (双向包含，要求被包含的词>=4字符)
        for key, team in self._index.items():
            if len(norm) >= 5 and norm in key:
                return team
            if len(key) >= 5 and key in norm:
                return team

        # 3) 词重叠匹配
        norm_words = set(norm.replace("-", " ").split())
        for key, team in self._index.items():
            key_words = set(key.replace("-", " ").split())
            overlap = norm_words & key_words
            if any(len(w) >= 5 for w in overlap):
                return team

        # 4) SequenceMatcher (严格阈值)
        best_score, best_team = 0, None
        for key, team in self._index.items():
            score = SequenceMatcher(None, norm, key).ratio()
            if score > best_score:
                best_score, best_team = score, team
        if best_score >= 0.85:
            return best_team

        # 5) 自动创建
        if auto_create:
            return self._create_team(of_name)

        return None


# ─── 比赛解析 ───

def parse_match(m_data: dict, season: str, competition: str) -> dict | None:
    date_str = m_data.get("date", "")
    time_str = m_data.get("time", "15:00")
    try:
        dt = datetime.strptime(f"{date_str}T{time_str}:00", "%Y-%m-%dT%H:%M:%S")
        kickoff_utc = dt.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None

    score = m_data.get("score", {})
    ft = score.get("ft", {})
    ht = score.get("ht", {})

    hg = ag = ht_h = ht_a = None
    if isinstance(ft, list) and len(ft) == 2:
        try:
            hg, ag = int(ft[0]), int(ft[1])
        except (ValueError, TypeError):
            pass
    if isinstance(ht, list) and len(ht) == 2:
        try:
            ht_h, ht_a = int(ht[0]), int(ht[1])
        except (ValueError, TypeError):
            pass

    return {
        "kickoff_utc": kickoff_utc.isoformat(),
        "team1": m_data.get("team1", ""),
        "team2": m_data.get("team2", ""),
        "home_goals": hg, "away_goals": ag,
        "ht_home": ht_h, "ht_away": ht_a,
        "round": m_data.get("round", ""),
        "competition": competition, "season": season,
    }


# ─── 历史回填 ───

def import_historical(db_path: str, local_root: Path | None = None, dry_run: bool = False):
    conn = sqlite3.connect(db_path)
    matcher = TeamMatcher(db_path, conn)
    cur = conn.cursor()

    total_added = total_skipped = total_unmatched = 0
    unmatched_teams = set()

    for season in BACKFILL_SEASONS:
        for league_code in BACKFILL_LEAGUES:
            comp = LEAGUE_MAP.get(league_code, league_code)

            if local_root:
                data = fetch_json_local(local_root, season, league_code)
                source = f"local:{local_root}/{season}/{league_code}.json"
            else:
                url = f"{BASE_URL}/{season}/{league_code}.json"
                data = fetch_json_online(url)
                source = url

            if not data:
                print(f"  {season}/{league_code} ({comp}): SKIP")
                continue

            matches = data.get("matches", [])
            added = skipped = unmatched = 0

            for m_data in matches:
                parsed = parse_match(m_data, season, comp)
                if not parsed:
                    skipped += 1
                    continue

                home_team = matcher.match(parsed["team1"])
                away_team = matcher.match(parsed["team2"])

                if not home_team:
                    unmatched += 1
                    unmatched_teams.add(parsed["team1"])
                    continue
                if not away_team:
                    unmatched += 1
                    unmatched_teams.add(parsed["team2"])
                    continue

                match_code = f"OF-{season}-{league_code}-{parsed['kickoff_utc'][:10]}-{home_team['code']}-{away_team['code']}"

                cur.execute("SELECT id FROM matches WHERE match_code = ?", (match_code,))
                if cur.fetchone():
                    skipped += 1
                    continue

                status = "finished" if parsed["home_goals"] is not None else "scheduled"
                outcome = None
                if parsed["home_goals"] is not None:
                    if parsed["home_goals"] > parsed["away_goals"]:
                        outcome = "home"
                    elif parsed["home_goals"] == parsed["away_goals"]:
                        outcome = "draw"
                    else:
                        outcome = "away"

                if not dry_run:
                    cur.execute("""
                        INSERT INTO matches
                        (match_code, home_team_id, away_team_id, kickoff_at,
                         competition, status, actual_home_goals, actual_away_goals,
                         actual_outcome, match_type, ht_home_goals, ht_away_goals)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (match_code, home_team["id"], away_team["id"],
                          parsed["kickoff_utc"], comp, status,
                          parsed["home_goals"], parsed["away_goals"],
                          outcome, "FRIENDLY",
                  parsed.get("ht_home"), parsed.get("ht_away")))
                added += 1

            if not dry_run:
                conn.commit()

            print(f"  {season}/{league_code} ({comp}): {len(matches)} matches → +{added} added, {skipped} skip, {unmatched} no-match")
            total_added += added
            total_skipped += skipped
            total_unmatched += unmatched

    if unmatched_teams:
        print(f"\n  未匹配队名 ({len(unmatched_teams)}):")
        for t in sorted(unmatched_teams)[:20]:
            print(f"    - {t}")
        if len(unmatched_teams) > 20:
            print(f"    ... 还有 {len(unmatched_teams)-20} 个")

    print(f"\n  === 历史回填: +{total_added} | skip={total_skipped} | no-match={total_unmatched} ===")
    conn.close()


# ─── 当赛季结果同步 ───

def sync_current_results(db_path: str, local_root: Path | None = None, dry_run: bool = False):
    conn = sqlite3.connect(db_path)
    matcher = TeamMatcher(db_path, conn)
    cur = conn.cursor()

    synced = 0

    for league_code in SYNC_LEAGUES:
        comp = LEAGUE_MAP.get(league_code, league_code)

        if local_root:
            data = fetch_json_local(local_root, SYNC_SEASON, league_code)
        else:
            data = fetch_json_online(f"{BASE_URL}/{SYNC_SEASON}/{league_code}.json")

        if not data:
            continue

        finished = [m for m in data.get("matches", []) if m.get("score", {}).get("ft")]

        for m_data in finished:
            parsed = parse_match(m_data, SYNC_SEASON, comp)
            if not parsed or parsed["home_goals"] is None:
                continue

            home_team = matcher.match(parsed["team1"])
            away_team = matcher.match(parsed["team2"])
            if not home_team or not away_team:
                continue

            # 在数据库中找对应比赛
            cur.execute("""
                SELECT id, actual_home_goals, actual_away_goals, status, ht_home_goals, ht_away_goals
                FROM matches
                WHERE home_team_id = ? AND away_team_id = ?
                  AND DATE(kickoff_at) = DATE(?)
                LIMIT 1
            """, (home_team["id"], away_team["id"], parsed["kickoff_utc"]))

            row = cur.fetchone()
            if not row:
                continue

            match_id, db_hg, db_ag, db_status, db_ht_h, db_ht_a = row[0], row[1], row[2], row[3], row[4], row[5]
            if db_status == "finished" and db_hg is not None:
                continue

            outcome = "home" if parsed["home_goals"] > parsed["away_goals"] else (
                "draw" if parsed["home_goals"] == parsed["away_goals"] else "away")

            if not dry_run:
                cur.execute("""
                    UPDATE matches
                    SET status = 'FINISHED',
                        actual_home_goals = ?, actual_away_goals = ?,
                        actual_outcome = ?,
                ht_home_goals = ?, ht_away_goals = ?
                WHERE id = ?
                """, (parsed["home_goals"], parsed["away_goals"], outcome,
                  parsed.get("ht_home"), parsed.get("ht_away"), match_id))
            synced += 1

    if not dry_run:
        conn.commit()

    print(f"  === 结果同步: {synced} 场更新 ===")
    conn.close()


# ─── 主入口 ───

def main():
    parser = argparse.ArgumentParser(description="openfootball/football.json 数据导入器")
    parser.add_argument("--backfill", action="store_true", help="回填历史赛季 (2010-2025)")
    parser.add_argument("--sync-results", action="store_true", help="同步当赛季结果 (2025-26)")
    parser.add_argument("--full", action="store_true", help="完整导入 (backfill + sync)")
    parser.add_argument("--local", type=str, metavar="DIR",
                        help="本地数据目录 (git clone 的 football.json 仓库根目录)")
    parser.add_argument("--dry-run", action="store_true", help="Dry run (只看不写)")
    args = parser.parse_args()

    if not any([args.backfill, args.sync_results, args.full]):
        parser.print_help()
        sys.exit(1)

    db = str(DB_PATH)
    local_root = Path(args.local) if args.local else None

    print(f"数据库: {db}")
    print(f"数据源: {'本地 ' + str(local_root) if local_root else '在线 (GitHub)'}")
    print(f"模式: {'DRY RUN' if args.dry_run else 'LIVE'}")

    if args.full or args.backfill:
        print(f"\n{'='*50}")
        print("  历史数据回填 (2010-11 → 2024-25)")
        print(f"{'='*50}")
        import_historical(db, local_root, dry_run=args.dry_run)

    if args.full or args.sync_results:
        print(f"\n{'='*50}")
        print(f"  当赛季结果同步 ({SYNC_SEASON})")
        print(f"{'='*50}")
        sync_current_results(db, local_root, dry_run=args.dry_run)

    print("\nDone.")


if __name__ == "__main__":
    main()
