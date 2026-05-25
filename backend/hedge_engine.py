"""
对冲引擎 — 跨庄套利扫描 + 对冲比率计算 + Dutch book。

1. 跨庄套利：从 MatchBookmakerOdds 中找不同庄家的最优赔率，
   如果 sum(1/best_odds) < 1 则存在套利机会。

2. 对冲比率：已有仓位后，计算反向投注比例以锁定利润或减少损失。

3. Dutch book：多选项组合投注，保证无论哪个结果都赢。

用法:
from hedge_engine import HedgeEngine
engine = HedgeEngine(db_session)

# 套利扫描
opps = engine.scan_arbitrage(competition="EPL")

# 对冲计算
hedge = engine.compute_hedge(
    original_odds=1.80, original_stake=50.0,
    hedge_odds=2.20, outcome="away",
)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timezone, timedelta

from sqlalchemy.orm import Session
from sqlalchemy import and_

from logger import get_logger

logger = get_logger("hedge_engine")


# ─── 数据结构 ───

@dataclass(frozen=True)
class ArbitrageOpportunity:
    """跨庄套利机会"""
    match_id: int
    best_home_odds: float
    best_draw_odds: float
    best_away_odds: float
    home_bookmaker: str
    draw_bookmaker: str
    away_bookmaker: str
    implied_total: float       # sum(1/best_odds)，< 1 则有套利
    profit_pct: float          # (1 / implied_total - 1)，套利利润率
    stakes: Dict[str, float]   # 每个选项投注金额（以 100 单位为例）
    is_genuine: bool           # 排除交易成本后是否仍有利润
    net_profit_pct: float      # 扣除交易成本后的净利润率


@dataclass(frozen=True)
class HedgeResult:
    """对冲计算结果"""
    hedge_stake: float         # 对冲投注金额
    hedge_odds: float          # 对冲赔率
    guaranteed_profit: float   # 锁定利润（无论结果）
    hedge_ratio: float         # 对冲比率 (hedge_stake / original_stake)
    profit_if_original_wins: float
    profit_if_hedge_wins: float
    is_profitable: bool        # 对冲后是否锁定正利润


@dataclass(frozen=True)
class DutchBookResult:
    """Dutch book 计算结果"""
    total_stake: float
    stakes: Dict[str, float]   # {selection: amount}
    profit_per_selection: Dict[str, float]
    is_arbitrage: bool
    profit_pct: float


# ─── 配置 ───

# 交易成本：竞彩约 8-10%，欧洲庄约 2-5%
TRANSACTION_COST_JINGCAI = 0.08
TRANSACTION_COST_EUROPEAN = 0.03

# 套利最低利润阈值（扣除交易成本后）
MIN_ARB_PROFIT_PCT = 0.005  # 0.5% 净利润才有意义


# ─── 套利检测 ───

def compute_implied_total(home: float, draw: float, away: float) -> float:
    """计算隐含概率总和: sum(1/odds)。< 1 表示存在套利。"""
    if home <= 1.0 or draw <= 1.0 or away <= 1.0:
        return 999.0
    return 1.0 / home + 1.0 / draw + 1.0 / away


def compute_arb_stakes(
    home: float, draw: float, away: float,
    total_bankroll: float = 100.0,
) -> Dict[str, float]:
    """
    计算套利投注分配。

    每个选项投注额 = total_bankroll × (1/odds) / implied_total
    这样无论哪个结果，总回报 = total_bankroll / implied_total
    """
    imp = compute_implied_total(home, draw, away)
    if imp >= 1.0 or imp <= 0:
        return {"home": 0, "draw": 0, "away": 0}

    s_home = total_bankroll * (1.0 / home) / imp
    s_draw = total_bankroll * (1.0 / draw) / imp
    s_away = total_bankroll * (1.0 / away) / imp

    return {"home": round(s_home, 2), "draw": round(s_draw, 2), "away": round(s_away, 2)}


def adjust_for_transaction_cost(gross_profit_pct: float, cost: float) -> float:
    """扣除交易成本后的净利润率。"""
    return gross_profit_pct - cost


class HedgeEngine:
    """
    对冲引擎。

    用法:
    engine = HedgeEngine(db_session)
    opps = engine.scan_arbitrage()
    hedge = engine.compute_hedge(1.80, 50.0, 2.20, "away")
    """

    def __init__(self, session: Session):
        self._session = session

    # ─── 套利扫描 ───

    def scan_arbitrage(
        self,
        competition: str = "",
        min_profit_pct: float = MIN_ARB_PROFIT_PCT,
        transaction_cost: float = TRANSACTION_COST_EUROPEAN,
    ) -> List[ArbitrageOpportunity]:
        """
        扫描跨庄套利机会。

        从 MatchBookmakerOdds 找出每场比赛各庄家的最优赔率，
        检测是否存在 sum(1/best_odds) < 1 的套利窗口。
        """
        from models import Match, MatchBookmakerOdds
        from sqlalchemy import func
        from collections import defaultdict

        # 批量加载所有比赛和赔率
        match_query = self._session.query(Match.id).filter(
            Match.status.in_(["scheduled", "upcoming", "live"])
        )
        if competition:
            match_query = match_query.filter(Match.competition == competition)

        match_ids = [r[0] for r in match_query.all()]
        if not match_ids:
            return []

        # 一次性加载所有 bookmaker odds
        all_odds = self._session.query(MatchBookmakerOdds).filter(
            MatchBookmakerOdds.match_id.in_(match_ids),
        ).all()

        # 按 match_id 分组
        by_match: Dict[int, List] = defaultdict(list)
        for bo in all_odds:
            by_match[bo.match_id].append(bo)

        opportunities: List[ArbitrageOpportunity] = []

        for mid, book_odds in by_match.items():
            opp = self._check_arbitrage_from_records(mid, book_odds, transaction_cost, min_profit_pct)
            if opp is not None:
                opportunities.append(opp)

        opportunities.sort(key=lambda o: o.net_profit_pct, reverse=True)
        return opportunities

    def _check_arbitrage_from_records(
        self,
        match_id: int,
        book_odds_list: list,
        transaction_cost: float,
        min_profit_pct: float,
    ) -> Optional[ArbitrageOpportunity]:
        """从已加载的 bookmaker odds 记录中检查套利。"""
        best_home = best_draw = best_away = 0.0
        home_bk = draw_bk = away_bk = ""

        for bo in book_odds_list:
            if bo.odds_home > best_home:
                best_home = bo.odds_home
                home_bk = bo.bookmaker
            if bo.odds_draw > best_draw:
                best_draw = bo.odds_draw
                draw_bk = bo.bookmaker
            if bo.odds_away > best_away:
                best_away = bo.odds_away
                away_bk = bo.bookmaker

        if best_home <= 1.0 or best_draw <= 1.0 or best_away <= 1.0:
            return None

        implied = compute_implied_total(best_home, best_draw, best_away)
        if implied >= 1.0:
            return None

        gross_profit = (1.0 / implied - 1.0)
        net_profit = adjust_for_transaction_cost(gross_profit, transaction_cost)
        stakes = compute_arb_stakes(best_home, best_draw, best_away)

        if net_profit < min_profit_pct:
            return None

        return ArbitrageOpportunity(
            match_id=match_id,
            best_home_odds=best_home,
            best_draw_odds=best_draw,
            best_away_odds=best_away,
            home_bookmaker=home_bk,
            draw_bookmaker=draw_bk,
            away_bookmaker=away_bk,
            implied_total=implied,
            profit_pct=gross_profit,
            stakes=stakes,
            is_genuine=net_profit > 0,
            net_profit_pct=net_profit,
        )

    def _check_match_arbitrage(
        self,
        match_id: int,
        transaction_cost: float,
        min_profit_pct: float,
    ) -> Optional[ArbitrageOpportunity]:
        """检查单场比赛是否有套利机会。"""
        from models import MatchBookmakerOdds

        book_odds = (
            self._session.query(MatchBookmakerOdds)
            .filter(MatchBookmakerOdds.match_id == match_id)
            .all()
        )

        if not book_odds:
            return None

        # 找每个选项的最优赔率
        best_home = best_draw = best_away = 0.0
        home_bk = draw_bk = away_bk = ""

        for bo in book_odds:
            if bo.odds_home > best_home:
                best_home = bo.odds_home
                home_bk = bo.bookmaker
            if bo.odds_draw > best_draw:
                best_draw = bo.odds_draw
                draw_bk = bo.bookmaker
            if bo.odds_away > best_away:
                best_away = bo.odds_away
                away_bk = bo.bookmaker

        if best_home <= 1.0 or best_draw <= 1.0 or best_away <= 1.0:
            return None

        implied = compute_implied_total(best_home, best_draw, best_away)
        if implied >= 1.0:
            return None

        gross_profit = (1.0 / implied - 1.0)
        net_profit = adjust_for_transaction_cost(gross_profit, transaction_cost)
        stakes = compute_arb_stakes(best_home, best_draw, best_away)

        if net_profit < min_profit_pct:
            return None

        return ArbitrageOpportunity(
            match_id=match_id,
            best_home_odds=best_home,
            best_draw_odds=best_draw,
            best_away_odds=best_away,
            home_bookmaker=home_bk,
            draw_bookmaker=draw_bk,
            away_bookmaker=away_bk,
            implied_total=implied,
            profit_pct=gross_profit,
            stakes=stakes,
            is_genuine=net_profit > 0,
            net_profit_pct=net_profit,
        )

    # ─── 对冲计算 ───

    @staticmethod
    def compute_hedge(
        original_odds: float,
        original_stake: float,
        hedge_odds: float,
        hedge_outcome: str = "away",
    ) -> HedgeResult:
        """
        计算对冲投注。

        假设你已经在 original_odds 上投了 original_stake，
        现在 hedge_outcome 的赔率是 hedge_odds，
        计算需要投多少才能锁定利润。

        对冲金额 = (original_odds × original_stake) / hedge_odds

        这样:
        - 如果 original 赢: 利润 = original_odds × stake - stake - hedge_stake
        - 如果 hedge 赢: 利润 = hedge_odds × hedge_stake - hedge_stake - original_stake
        """
        if original_odds <= 1.0 or hedge_odds <= 1.0 or original_stake <= 0:
            return HedgeResult(
                hedge_stake=0, hedge_odds=hedge_odds,
                guaranteed_profit=0, hedge_ratio=0,
                profit_if_original_wins=0, profit_if_hedge_wins=0,
                is_profitable=False,
            )

        original_payout = original_odds * original_stake
        hedge_stake = original_payout / hedge_odds

        profit_original_wins = original_payout - original_stake - hedge_stake
        profit_hedge_wins = (hedge_odds * hedge_stake) - hedge_stake - original_stake

        guaranteed = min(profit_original_wins, profit_hedge_wins)

        return HedgeResult(
            hedge_stake=round(hedge_stake, 2),
            hedge_odds=hedge_odds,
            guaranteed_profit=round(guaranteed, 2),
            hedge_ratio=round(hedge_stake / original_stake, 3),
            profit_if_original_wins=round(profit_original_wins, 2),
            profit_if_hedge_wins=round(profit_hedge_wins, 2),
            is_profitable=guaranteed > 0,
        )

    @staticmethod
    def compute_partial_hedge(
        original_odds: float,
        original_stake: float,
        hedge_odds: float,
        hedge_fraction: float = 0.5,
    ) -> HedgeResult:
        """
        部分对冲：只对冲部分仓位，保留部分上行空间。

        hedge_fraction: 0.0 = 不对冲, 1.0 = 完全对冲
        """
        full_hedge = HedgeEngine.compute_hedge(
            original_odds, original_stake, hedge_odds,
        )
        partial_stake = full_hedge.hedge_stake * hedge_fraction

        original_payout = original_odds * original_stake
        profit_original_wins = original_payout - original_stake - partial_stake
        profit_hedge_wins = (hedge_odds * partial_stake) - partial_stake - original_stake

        return HedgeResult(
            hedge_stake=round(partial_stake, 2),
            hedge_odds=hedge_odds,
            guaranteed_profit=round(min(profit_original_wins, profit_hedge_wins), 2),
            hedge_ratio=round(partial_stake / original_stake, 3) if original_stake > 0 else 0,
            profit_if_original_wins=round(profit_original_wins, 2),
            profit_if_hedge_wins=round(profit_hedge_wins, 2),
            is_profitable=min(profit_original_wins, profit_hedge_wins) > 0,
        )

    # ─── Dutch Book ───

    @staticmethod
    def dutch_book(
        odds_map: Dict[str, float],
        total_stake: float = 100.0,
    ) -> DutchBookResult:
        """
        Dutch book 计算：给定多个选项的赔率，计算各选项投注额
        使得无论哪个结果，回报相同。

        Args:
            odds_map: {"home": 1.80, "draw": 3.50, "away": 4.20}
            total_stake: 总投注额

        Returns:
            DutchBookResult with per-selection stakes and profit
        """
        if not odds_map:
            return DutchBookResult(
                total_stake=0, stakes={}, profit_per_selection={},
                is_arbitrage=False, profit_pct=0,
            )

        implied = sum(1.0 / o for o in odds_map.values() if o > 1.0)
        if implied <= 0:
            return DutchBookResult(
                total_stake=total_stake, stakes={}, profit_per_selection={},
                is_arbitrage=False, profit_pct=0,
            )

        stakes: Dict[str, float] = {}
        profit_per: Dict[str, float] = {}
        is_arb = implied < 1.0
        profit_pct = (1.0 / implied - 1.0) if is_arb else 0.0

        for sel, odds in odds_map.items():
            if odds <= 1.0:
                stakes[sel] = 0.0
                profit_per[sel] = -total_stake
                continue
            s = total_stake * (1.0 / odds) / implied
            stakes[sel] = round(s, 2)
            payout = odds * s
            profit_per[sel] = round(payout - total_stake, 2)

        return DutchBookResult(
            total_stake=total_stake,
            stakes=stakes,
            profit_per_selection=profit_per,
            is_arbitrage=is_arb,
            profit_pct=profit_pct,
        )

    # ─── 竞彩对冲专用 ───

    @staticmethod
    def jingcai_hedge(
        original_selection: str,
        original_odds: float,
        original_stake: float,
        other_odds: Dict[str, float],
        max_total_stake: float = 200.0,
    ) -> Optional[DutchBookResult]:
        """
        竞彩对冲：已有竞彩单选，计算是否可以跨选项对冲。

        竞彩的限制：同一玩法同一场不能同时买多个选项。
        所以只能通过不同玩法对冲（如 SPF 主胜 + RQ 客胜）。

        此方法简化处理：计算对冲其他选项的最优分配。
        """
        all_odds = {original_selection: original_odds}
        all_odds.update(other_odds)

        result = HedgeEngine.dutch_book(all_odds, original_stake)

        if result.total_stake > max_total_stake:
            return None

        return result
