import os
import sys
import json

# 将 research/src 添加到路径
sys.path.append(os.path.join(os.getcwd(), 'research', 'src'))

from footy.content.engine import WorldCupContentEngine

def main():
    print("🏆 2026 世界杯赛事内容引擎 - 演示")
    print("="*50)
    
    engine = WorldCupContentEngine()
    
    # 模拟一场世界杯比赛: 阿根廷 vs 法国
    card = engine.generate_match_preview(
        match_id=12345,
        home_team="Argentina",
        away_team="France"
    )
    
    print(f"\n📍 比赛: {card['match_info']['pairing']}")
    print(f"🏟️ 场馆: {card['match_info']['venue']}")
    
    print("\n📊 核心数据视角 (Stats Insights):")
    print(f"  - 历史平均 xG (预期进球):")
    print(f"    {card['match_info']['pairing'].split(' vs ')[0]}: {card['stats']['avg_xg']['home']}")
    print(f"    {card['match_info']['pairing'].split(' vs ')[1]}: {card['stats']['avg_xg']['away']}")
    print(f"  - 历史交锋 (H2H): {card['stats']['h2h']}")
    
    print("\n🤖 AI 战术前瞻 (AI Analysis):")
    print(f"  【{card['ai_analysis']['headline']}】")
    print(f"  {card['ai_analysis']['content']}")
    
    print("\n📈 数据预测参考 (Model Reference):")
    print(f"  - 主胜: {card['prediction_ref']['home_win']:.1%}")
    print(f"  - 平局: {card['prediction_ref']['draw']:.1%}")
    print(f"  - 客胜: {card['prediction_ref']['away_win']:.1%}")
    
    print("\n" + "="*50)
    print("✅ 内容生成完成。这些数据可直接对接前端可视化组件或生成社交媒体图文。")

if __name__ == "__main__":
    main()
