"""
2018 世界杯赔率更新工具

功能：
  1. generate  — 基于 ELO + 冠军赔率生成更合理的 1X2 估算赔率
  2. import    — 读取用户手动整理的真实赔率 CSV，替换回测数据
  3. compare   — 对比三种来源的赔率差异

用法：
    cd backend
    python update_2018_odds.py generate    # 生成估算赔率
    python update_2018_odds.py compare     # 对比估算 vs 当前
    python update_2018_odds.py import data/wc2018_manual_odds.csv  # 导入真实赔率

手动整理 CSV 格式（64行，无表头）：
    home_code,away_code,home_goals,away_goals,odds_home,odds_draw,odds_away,stage,is_knockout
"""

from __future__ import annotations

import sys
import math
import re
import csv
from pathlib import Path
from typing import List, Tuple, Dict

# ─────────────────────────────────────────
# 常量
# ─────────────────────────────────────────

OUTRIGHT_ODDS_PATH = Path(__file__).parent / "data" / "wc2018_outright_odds.csv"
BACKTEST_FILE = Path(__file__).parent / "backtest_2018_wc.py"

TEAM_NAME_TO_CODE = {
    "Brazil": "BRA",
    "Germany": "GER",
    "Spain": "ESP",
    "France": "FRA",
    "Argentina": "ARG",
    "Belgium": "BEL",
    "England": "ENG",
    "Portugal": "POR",
    "Uruguay": "URU",
    "Croatia": "CRO",
    "Colombia": "COL",
    "Russia": "RUS",
    "Poland": "POL",
    "Denmark": "DEN",
    "Switzerland": "SUI",
    "Mexico": "MEX",
    "Sweden": "SWE",
    "Egypt": "EGY",
    "Serbia": "SRB",
    "Senegal": "SEN",
    "Nigeria": "NGA",
    "Peru": "PER",
    "Iceland": "ISL",
    "Japan": "JPN",
    "Australia": "AUS",
    "Costa Rica": "CRC",
    "Morocco": "MAR",
    "Iran": "IRN",
    "South Korea": "KOR",
    "Tunisia": "TUN",
    "Panama": "PAN",
    "Saudi Arabia": "KSA",
}

# 2018 世界杯 ELO（赛前，基于 ClubElo 近似值）
TEAM_ELO_2018 = {
    "GER": 1985, "BRA": 1980, "BEL": 1930, "POR": 1910, "ARG": 1890,
    "ESP": 1880, "FRA": 1860, "ENG": 1840, "URU": 1830, "CRO": 1810,
    "COL": 1790, "MEX": 1770, "SUI": 1750, "DEN": 1740, "SWE": 1720,
    "RUS": 1710, "SRB": 1700, "POL": 1690, "SEN": 1680, "PER": 1670,
    "NGA": 1660, "JPN": 1650, "IRN": 1640, "ISL": 1630, "KOR": 1620,
    "CRC": 1610, "AUS": 1600, "MAR": 1590, "EGY": 1580, "TUN": 1570,
    "PAN": 1450, "KSA": 1440,
}

# 从 backtest_2018_wc.py 导入的当前估算赔率
# 格式: (home_code, away_code, home_goals, away_goals, odds_home, odds_draw, odds_away, stage, is_knockout)
CURRENT_MATCHES: List[Tuple[str, str, int, int, float, float, float, str, bool]] = [
    ("RUS", "KSA", 5, 0, 1.45, 4.20, 8.50, "group", False),
    ("EGY", "URU", 0, 1, 4.50, 3.20, 1.95, "group", False),
    ("RUS", "EGY", 3, 1, 1.90, 3.40, 4.20, "group", False),
    ("URU", "KSA", 1, 0, 1.30, 5.00, 11.00, "group", False),
    ("URU", "RUS", 3, 0, 2.60, 3.20, 2.90, "group", False),
    ("KSA", "EGY", 2, 1, 3.40, 3.20, 2.30, "group", False),
    ("MAR", "IRN", 0, 1, 2.70, 3.00, 2.90, "group", False),
    ("POR", "ESP", 3, 3, 3.40, 3.20, 2.25, "group", False),
    ("POR", "MAR", 1, 0, 1.55, 3.80, 7.00, "group", False),
    ("IRN", "ESP", 0, 1, 11.00, 5.00, 1.30, "group", False),
    ("IRN", "POR", 1, 1, 6.50, 3.80, 1.55, "group", False),
    ("ESP", "MAR", 2, 2, 1.35, 4.80, 10.00, "group", False),
    ("FRA", "AUS", 2, 1, 1.22, 6.50, 15.00, "group", False),
    ("PER", "DEN", 0, 1, 3.60, 3.20, 2.20, "group", False),
    ("DEN", "AUS", 1, 1, 2.10, 3.30, 3.60, "group", False),
    ("FRA", "PER", 1, 0, 1.18, 7.00, 18.00, "group", False),
    ("DEN", "FRA", 0, 0, 7.00, 4.20, 1.50, "group", False),
    ("AUS", "PER", 0, 2, 2.60, 3.20, 2.90, "group", False),
    ("ARG", "ISL", 1, 1, 1.28, 5.50, 12.00, "group", False),
    ("CRO", "NGA", 2, 0, 1.75, 3.50, 5.20, "group", False),
    ("ARG", "CRO", 0, 3, 1.85, 3.40, 4.50, "group", False),
    ("NGA", "ISL", 2, 0, 2.40, 3.20, 3.10, "group", False),
    ("NGA", "ARG", 1, 2, 6.50, 4.20, 1.50, "group", False),
    ("ISL", "CRO", 1, 2, 5.50, 3.60, 1.70, "group", False),
    ("CRC", "SRB", 0, 1, 3.60, 3.20, 2.20, "group", False),
    ("BRA", "SUI", 1, 1, 1.40, 4.50, 9.50, "group", False),
    ("BRA", "CRC", 2, 0, 1.12, 8.50, 25.00, "group", False),
    ("SUI", "SRB", 2, 1, 2.30, 3.20, 3.30, "group", False),
    ("SRB", "BRA", 0, 2, 8.50, 4.50, 1.40, "group", False),
    ("SUI", "CRC", 2, 2, 1.70, 3.60, 5.50, "group", False),
    ("GER", "MEX", 0, 1, 1.50, 4.20, 7.00, "group", False),
    ("SWE", "KOR", 1, 0, 2.30, 3.20, 3.30, "group", False),
    ("KOR", "MEX", 1, 2, 4.50, 3.60, 1.80, "group", False),
    ("GER", "SWE", 2, 1, 1.40, 4.50, 9.00, "group", False),
    ("KOR", "GER", 2, 0, 12.00, 6.00, 1.25, "group", False),
    ("MEX", "SWE", 0, 3, 2.90, 3.20, 2.60, "group", False),
    ("BEL", "PAN", 3, 0, 1.15, 7.50, 22.00, "group", False),
    ("TUN", "ENG", 1, 2, 5.50, 3.80, 1.65, "group", False),
    ("BEL", "TUN", 5, 2, 1.18, 7.00, 18.00, "group", False),
    ("ENG", "PAN", 6, 1, 1.12, 8.50, 25.00, "group", False),
    ("ENG", "BEL", 0, 1, 3.00, 3.40, 2.40, "group", False),
    ("PAN", "TUN", 1, 2, 3.20, 3.20, 2.40, "group", False),
    ("COL", "JPN", 1, 2, 1.95, 3.30, 4.20, "group", False),
    ("POL", "SEN", 1, 2, 1.95, 3.30, 4.20, "group", False),
    ("JPN", "SEN", 2, 2, 2.60, 3.20, 2.90, "group", False),
    ("POL", "COL", 0, 3, 2.90, 3.20, 2.60, "group", False),
    ("JPN", "POL", 0, 1, 3.20, 3.30, 2.30, "group", False),
    ("SEN", "COL", 0, 1, 3.00, 3.20, 2.50, "group", False),
    ("FRA", "ARG", 4, 3, 2.00, 3.30, 4.00, "R16", True),
    ("URU", "POR", 2, 1, 2.90, 3.10, 2.60, "R16", True),
    ("ESP", "RUS", 1, 1, 1.60, 3.80, 6.50, "R16", True),
    ("CRO", "DEN", 1, 1, 1.90, 3.30, 4.50, "R16", True),
    ("BRA", "MEX", 2, 0, 1.35, 4.80, 11.00, "R16", True),
    ("BEL", "JPN", 3, 2, 1.30, 5.50, 12.00, "R16", True),
    ("SWE", "SUI", 1, 0, 2.80, 2.90, 2.90, "R16", True),
    ("ENG", "COL", 1, 1, 1.85, 3.40, 4.50, "R16", True),
    ("URU", "FRA", 0, 2, 3.60, 3.20, 2.20, "QF", True),
    ("BRA", "BEL", 1, 2, 1.75, 3.60, 5.00, "QF", True),
    ("SWE", "ENG", 0, 2, 4.20, 3.20, 2.00, "QF", True),
    ("RUS", "CRO", 2, 2, 3.80, 3.20, 2.10, "QF", True),
    ("FRA", "BEL", 1, 0, 2.10, 3.30, 3.60, "SF", True),
    ("CRO", "ENG", 2, 1, 2.90, 3.10, 2.60, "SF", True),
    ("BEL", "ENG", 2, 0, 2.40, 3.40, 3.00, "3P", True),
    ("FRA", "CRO", 4, 2, 1.75, 3.60, 5.20, "F", True),
]


# ─────────────────────────────────────────
# 冠军赔率 → 实力参数
# ─────────────────────────────────────────

def load_outright_odds() -> Dict[str, float]:
    """加载冠军赔率，返回 {team_code: odds}"""
    odds_map = {}
    with open(OUTRIGHT_ODDS_PATH, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            name = row["team"]
            code = TEAM_NAME_TO_CODE.get(name)
            if code:
                odds_map[code] = float(row["outright_odds"])
    return odds_map


def calc_strength_ratings(outright_odds: Dict[str, float]) -> Dict[str, float]:
    """
    从冠军赔率反推球队实力分。
    方法：隐含概率 → 对数变换 → 标准化实力分
    """
    # 隐含概率（不归一化，直接用赔率倒数）
    inv_odds = {code: 1.0 / odds for code, odds in outright_odds.items()}
    # 转换为对数实力分（赔率越低，实力分越高）
    ratings = {}
    for code, inv in inv_odds.items():
        # 缩放使范围在 1500-2000 之间，与 ELO 可比
        ratings[code] = 1500 + 500 * (inv / max(inv_odds.values()))
    return ratings


# ─────────────────────────────────────────
# 比赛级别 1X2 赔率生成
# ─────────────────────────────────────────

def elo_expected_score(elo_a: float, elo_b: float) -> float:
    """ELO 预期 A 对 B 的得分率（胜=1, 平=0.5, 负=0）"""
    return 1.0 / (1.0 + 10.0 ** ((elo_b - elo_a) / 400.0))


def generate_match_odds(
    home_code: str,
    away_code: str,
    stage: str,
    outright: Dict[str, float],
    home_advantage: float = 65.0,  # 主场/种子优势 ELO 当量
) -> Tuple[float, float, float]:
    """
    基于 ELO + 冠军赔率生成 1X2 概率，再转换为十进制赔率。

    模型：
      - 综合 ELO = α*ELO_2018 + (1-α)*OutrightRating
      - 预期得分率 = elo_expected_score(综合ELO_home + home_adv, 综合ELO_away)
      - 平局概率 = base_draw * f(|ELO差|)  # 实力接近时平局概率上升
      - 主胜 = We * (1 - P_draw) + P_draw/3
      - 客胜 = (1-We) * (1 - P_draw) + P_draw/3
    """
    ratings = calc_strength_ratings(outright)

    # 综合评分（ELO 权重 70%，冠军赔率 30%）
    alpha = 0.7
    rh = alpha * TEAM_ELO_2018.get(home_code, 1500) + (1 - alpha) * ratings.get(home_code, 1500)
    ra = alpha * TEAM_ELO_2018.get(away_code, 1500) + (1 - alpha) * ratings.get(away_code, 1500)

    # 淘汰赛无明显主场优势（中立场地）
    if stage != "group":
        home_advantage = 0.0

    we = elo_expected_score(rh + home_advantage, ra)

    # 平局概率：基础 0.28，实力接近时上升到 0.35
    elo_diff = abs(rh - ra)
    draw_prob = 0.28 + 0.07 * math.exp(-elo_diff / 200.0)

    # 分配剩余概率
    home_prob = we * (1.0 - draw_prob)
    away_prob = (1.0 - we) * (1.0 - draw_prob)

    # 归一化（处理浮点误差）
    total = home_prob + draw_prob + away_prob
    home_prob /= total
    draw_prob /= total
    away_prob /= total

    # 转换为十进制赔率（加 5% 庄家 margin）
    margin = 1.05
    odds_home = margin / home_prob if home_prob > 0.01 else 50.0
    odds_draw = margin / draw_prob if draw_prob > 0.01 else 20.0
    odds_away = margin / away_prob if away_prob > 0.01 else 50.0

    # 限制范围
    odds_home = max(1.05, min(odds_home, 100.0))
    odds_draw = max(1.05, min(odds_draw, 20.0))
    odds_away = max(1.05, min(odds_away, 100.0))

    return round(odds_home, 2), round(odds_draw, 2), round(odds_away, 2)


# ─────────────────────────────────────────
# 模式 1: generate
# ─────────────────────────────────────────

def cmd_generate():
    """生成估算赔率并打印，不修改源文件"""
    outright = load_outright_odds()

    print("=" * 80)
    print("  2018 世界杯 — 基于 ELO + 冠军赔率的 1X2 赔率生成")
    print("=" * 80)
    print(f"\n  {'#':<4} {'对阵':<22} {'比分':<7} {'当前估算':<18} {'生成赔率':<18} {'差异'}")
    print("  " + "-" * 90)

    for i, (hc, ac, hg, ag, cur_oh, cur_od, cur_oa, stage, is_ko) in enumerate(CURRENT_MATCHES, 1):
        gen_oh, gen_od, gen_oa = generate_match_odds(hc, ac, stage, outright)

        # 计算差异（赔率比率的几何平均偏差）
        diff_h = gen_oh / cur_oh if cur_oh > 0 else 1.0
        diff_d = gen_od / cur_od if cur_od > 0 else 1.0
        diff_a = gen_oa / cur_oa if cur_oa > 0 else 1.0
        avg_diff = (abs(math.log(diff_h)) + abs(math.log(diff_d)) + abs(math.log(diff_a))) / 3.0
        flag = "⚠️" if avg_diff > 0.3 else ""

        matchup = f"{hc} vs {ac}"
        score = f"{hg}:{ag}"
        cur = f"{cur_oh:.2f}/{cur_od:.2f}/{cur_oa:.2f}"
        gen = f"{gen_oh:.2f}/{gen_od:.2f}/{gen_oa:.2f}"

        print(f"  {i:<4} {matchup:<22} {score:<7} {cur:<18} {gen:<18} {flag}")

    print("\n  提示: 将生成赔率保存到 CSV 后，可用 `import` 模式更新回测文件")


def cmd_export_generated():
    """生成估算赔率并保存为 CSV"""
    outright = load_outright_odds()
    out_path = Path(__file__).parent / "data" / "wc2018_generated_odds.csv"

    with open(out_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["home_code", "away_code", "home_goals", "away_goals",
                         "odds_home", "odds_draw", "odds_away", "stage", "is_knockout"])
        for hc, ac, hg, ag, _, _, _, stage, is_ko in CURRENT_MATCHES:
            gen_oh, gen_od, gen_oa = generate_match_odds(hc, ac, stage, outright)
            writer.writerow([hc, ac, hg, ag, gen_oh, gen_od, gen_oa, stage, int(is_ko)])

    print(f"✅ 生成赔率已保存到: {out_path}")
    print(f"   共 {len(CURRENT_MATCHES)} 场比赛")


# ─────────────────────────────────────────
# 模式 2: import
# ─────────────────────────────────────────

def cmd_import(csv_path: str):
    """读取用户手动整理的 CSV，替换 backtest_2018_wc.py 中的赔率"""
    csv_file = Path(csv_path)
    if not csv_file.exists():
        print(f"❌ 文件不存在: {csv_path}")
        sys.exit(1)

    # 读取手动赔率
    manual: List[Tuple[str, str, int, int, float, float, float, str, bool]] = []
    with open(csv_file, newline="") as f:
        reader = csv.reader(f)
        # 跳过表头（如果有）
        first = next(reader, None)
        if first and not first[0].isupper() and len(first) >= 9:
            # 有表头，跳过；如果第一行看起来像数据（大写code），加回去
            if first[0] in ["home_code", "team"]:
                pass  # 确实是表头
            else:
                manual.append(_parse_row(first))
        for row in reader:
            manual.append(_parse_row(row))

    if len(manual) != 64:
        print(f"⚠️  CSV 包含 {len(manual)} 行，预期 64 场")

    # 构建替换映射：key = (home_code, away_code)
    manual_map = {(m[0], m[1]): m for m in manual}

    # 读取源文件
    source = BACKTEST_FILE.read_text(encoding="utf-8")

    # 替换 WC2018_MATCHES 数组内容
    new_lines = []
    indent = "    "
    for m in CURRENT_MATCHES:
        key = (m[0], m[1])
        if key in manual_map:
            mm = manual_map[key]
            new_lines.append(
                f'{indent}("{mm[0]}", "{mm[1]}", {mm[2]}, {mm[3]}, '
                f'{mm[4]:.2f}, {mm[5]:.2f}, {mm[6]:.2f}, "{mm[7]}", {str(mm[8]).capitalize()}),'
            )
        else:
            # 保留原值
            new_lines.append(
                f'{indent}("{m[0]}", "{m[1]}", {m[2]}, {m[3]}, '
                f'{m[4]:.2f}, {m[5]:.2f}, {m[6]:.2f}, "{m[7]}", {str(m[8]).capitalize()}),'
            )

    # 在源文件中找到 WC2018_MATCHES 数组并替换
    # 简单做法：用正则匹配整个数组定义
    pattern = r'(WC2018_MATCHES: List\[Tuple\[.*?\]\] = \[)(.*?)(\n\])'
    replacement = r'\1\n' + '\n'.join(new_lines) + r'\n]'

    new_source = re.sub(pattern, replacement, source, flags=re.DOTALL)

    if new_source == source:
        print("⚠️  正则替换失败，尝试行级替换...")
        # 备用：逐行替换
        lines = source.splitlines()
        out_lines = []
        in_array = False
        array_idx = 0
        for line in lines:
            if 'WC2018_MATCHES: List' in line:
                in_array = True
                out_lines.append(line)
                continue
            if in_array and line.strip().startswith(']'):
                in_array = False
                out_lines.append(line)
                continue
            if in_array and line.strip().startswith('('):
                # 替换这一行
                m = CURRENT_MATCHES[array_idx]
                key = (m[0], m[1])
                if key in manual_map:
                    mm = manual_map[key]
                    indent = line[:len(line) - len(line.lstrip())]
                    out_lines.append(
                        f'{indent}("{mm[0]}", "{mm[1]}", {mm[2]}, {mm[3]}, '
                        f'{mm[4]:.2f}, {mm[5]:.2f}, {mm[6]:.2f}, "{mm[7]}", {str(mm[8]).capitalize()}),'
                    )
                else:
                    out_lines.append(line)
                array_idx += 1
            else:
                out_lines.append(line)
        new_source = '\n'.join(out_lines)

    # 写回
    BACKTEST_FILE.write_text(new_source, encoding="utf-8")
    print(f"✅ 已更新: {BACKTEST_FILE}")
    print(f"   替换了 {len([k for k in manual_map if any((c[0], c[1]) == k for c in CURRENT_MATCHES)])} 场比赛的赔率")


def _parse_row(row: List[str]) -> Tuple[str, str, int, int, float, float, float, str, bool]:
    """解析 CSV 行到元组"""
    is_ko = row[8].strip().lower() in ("true", "1", "yes", " knockout")
    return (
        row[0].strip().upper(),
        row[1].strip().upper(),
        int(row[2]),
        int(row[3]),
        float(row[4]),
        float(row[5]),
        float(row[6]),
        row[7].strip(),
        is_ko,
    )


# ─────────────────────────────────────────
# 模式 3: compare
# ─────────────────────────────────────────

def cmd_compare():
    """对比当前估算赔率 vs 生成赔率"""
    outright = load_outright_odds()

    print("=" * 90)
    print("  赔率对比：当前估算 vs ELO+冠军赔率生成")
    print("=" * 90)
    print(f"\n  {'#':<4} {'对阵':<20} {'当前估算':<20} {'生成赔率':<20} {'主胜变化':<10} {'平局变化':<10} {'客胜变化'}")
    print("  " + "-" * 100)

    large_diffs = 0
    for i, (hc, ac, hg, ag, cur_oh, cur_od, cur_oa, stage, is_ko) in enumerate(CURRENT_MATCHES, 1):
        gen_oh, gen_od, gen_oa = generate_match_odds(hc, ac, stage, outright)

        d_h = gen_oh - cur_oh
        d_d = gen_od - cur_od
        d_a = gen_oa - cur_oa

        # 标记变化超过 0.5 的
        flag = "🔴" if max(abs(d_h), abs(d_d), abs(d_a)) > 0.5 else ""
        if flag:
            large_diffs += 1

        matchup = f"{hc} vs {ac}"
        cur = f"{cur_oh:.2f}/{cur_od:.2f}/{cur_oa:.2f}"
        gen = f"{gen_oh:.2f}/{gen_od:.2f}/{gen_oa:.2f}"

        print(f"  {i:<4} {matchup:<20} {cur:<20} {gen:<20} {d_h:+6.2f}     {d_d:+6.2f}     {d_a:+6.2f} {flag}")

    print(f"\n  总计: {large_diffs} 场比赛赔率变化 > 0.5")
    print("  🔴 = 差异较大，建议核实")


# ─────────────────────────────────────────
# CLI
# ─────────────────────────────────────────

def print_usage():
    print("""
用法:
    python update_2018_odds.py generate          # 生成估算赔率并展示
    python update_2018_odds.py export            # 生成并保存到 CSV
    python update_2018_odds.py compare           # 对比当前 vs 生成
    python update_2018_odds.py import <csv>      # 导入手动整理的真实赔率

手动整理 CSV 格式:
    home_code,away_code,home_goals,away_goals,odds_home,odds_draw,odds_away,stage,is_knockout
    RUS,KSA,5,0,1.40,4.50,9.00,group,0
    EGY,URU,0,1,4.20,3.10,2.05,group,0
    ...
""")


def main():
    if len(sys.argv) < 2:
        print_usage()
        sys.exit(0)

    cmd = sys.argv[1].lower()

    if cmd == "generate":
        cmd_generate()
    elif cmd == "export":
        cmd_export_generated()
    elif cmd == "compare":
        cmd_compare()
    elif cmd == "import":
        if len(sys.argv) < 3:
            print("❌ 请提供 CSV 文件路径")
            sys.exit(1)
        cmd_import(sys.argv[2])
    else:
        print_usage()
        sys.exit(1)


if __name__ == "__main__":
    main()
