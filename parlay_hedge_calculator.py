
import argparse
import sys

def calculate_parlay_hedge(
    original_stake: float,
    accumulated_odds: float,
    final_leg_odds: float,
    hedge_odds: float,
    partial_hedge_ratio: float = 1.0
):
    """
    计算混合过关（串关）的末场对冲金额。
    
    :param original_stake: 串关的原始本金
    :param accumulated_odds: 已经红单的前几场赔率乘积（不包含最后一场）
    :param final_leg_odds: 最后一场你购买选项的赔率
    :param hedge_odds: 对冲选项（相反结果）的当前赔率
    :param partial_hedge_ratio: 对冲比例 (1.0 为完全对冲，锁定等额利润；< 1.0 保留更多原单上行空间)
    """
    # 串关全红时的总奖金
    potential_total_payout = original_stake * accumulated_odds * final_leg_odds
    
    # 理论上，为了让两边结果拿到的总奖金一样：
    # Payout_A = Payout_B
    # potential_total_payout = hedge_stake * hedge_odds
    
    # 完全对冲时的投入本金
    full_hedge_stake = potential_total_payout / hedge_odds
    
    # 实际投入的对冲本金
    actual_hedge_stake = full_hedge_stake * partial_hedge_ratio
    
    # 场景 1: 原串关全红
    profit_if_parlay_wins = potential_total_payout - original_stake - actual_hedge_stake
    
    # 场景 2: 对冲单红 (原串关黑)
    profit_if_hedge_wins = (actual_hedge_stake * hedge_odds) - original_stake - actual_hedge_stake
    
    return {
        "potential_payout": potential_total_payout,
        "hedge_stake": actual_hedge_stake,
        "profit_if_parlay_wins": profit_if_parlay_wins,
        "profit_if_hedge_wins": profit_if_hedge_wins,
        "is_profitable": profit_if_parlay_wins > 0 and profit_if_hedge_wins > 0
    }

def main():
    parser = argparse.ArgumentParser(description="中国足彩混合过关 (串关) 末场对冲计算器")
    parser.add_argument("--stake", type=float, required=True, help="串关原始本金 (元)")
    parser.add_argument("--acc-odds", type=float, required=True, help="已命中的前几场赔率乘积 (例如 1.5 * 1.8 = 2.7)")
    parser.add_argument("--final-odds", type=float, required=True, help="最后一场你购买选项的赔率")
    parser.add_argument("--hedge-odds", type=float, required=True, help="最后一场对冲选项的当前赔率 (如果是胜平负，通常找受让盘对冲)")
    parser.add_argument("--ratio", type=float, default=1.0, help="对冲比例 (默认 1.0 等额利润，0.5 为半血对冲)")
    
    args = parser.parse_args()
    
    res = calculate_parlay_hedge(
        args.stake, args.acc_odds, args.final_odds, args.hedge_odds, args.ratio
    )
    
    print("\n" + "="*50)
    print(" 🛡️  混合过关 (串关) 利润锁定方案")
    print("="*50)
    print(f"🔸 原始本金: ¥{args.stake:.2f}")
    print(f"🔸 已累计赔率: {args.acc_odds:.2f}")
    print(f"🔸 末场串关赔率: {args.final_odds:.2f}")
    print(f"🔸 期望总奖金: ¥{res['potential_payout']:.2f}")
    print("-" * 50)
    print(f"🔹 对冲目标赔率: {args.hedge_odds:.2f}")
    print(f"🔹 建议对冲金额: ¥{res['hedge_stake']:.2f} (对冲比例: {args.ratio:.0%})")
    print("-" * 50)
    
    status = "✅ 成功锁定正利润" if res['is_profitable'] else "⚠️ 无法完全无风险对冲 (赔率不足)"
    print(f"💡 对冲后结果模拟 ({status})")
    print(f"  👉 如果【原串关打出】: 净利润 ¥{res['profit_if_parlay_wins']:.2f}")
    print(f"  👉 如果【对冲单打出】: 净利润 ¥{res['profit_if_hedge_wins']:.2f}")
    print("="*50 + "\n")

if __name__ == "__main__":
    main()
