import os
import sys
import matplotlib.pyplot as plt
from statsbombpy import sb

# 将 research/src 添加到路径
sys.path.append(os.path.join(os.getcwd(), 'research', 'src'))

from footy.data.statsbomb import StatsBombLoader
from footy.evaluation.visualizer import MatchCardGenerator

def main():
    print("🎨 正在生成 2022 世界杯决赛内容卡 (Argentina vs France)...")
    
    match_id = 3869685 # 2022 世界杯决赛 ID
    loader = StatsBombLoader()
    
    print("📥 正在从 StatsBomb 拉取实时事件数据...")
    xg_data = loader.get_match_xg(match_id)
    print(f"✅ 获取到 xG 数据: {xg_data}")
    
    # 构建内容卡数据
    # 注意：在内容引擎中，我们会将这些“赛后”数据转化为“赛前”分析报告
    card_data = {
        "match_info": {
            "pairing": "Argentina vs France",
            "venue": "Lusail Stadium (2022 Final Case)"
        },
        "stats": {
            "avg_xg": {
                "home": xg_data.get('Argentina', 0),
                "away": xg_data.get('France', 0)
            },
            "h2h": "Classic Final"
        },
        "ai_analysis": {
            "headline": "梅西与姆巴佩的终极对决",
            "content": "本场比赛阿根廷在常规时间内创造了显著的 xG 优势，反映了其战术层面的压制。"
        },
        "prediction_ref": {
            "home_win": 0.35,
            "draw": 0.30,
            "away_win": 0.35
        }
    }
    
    print("🖌️ 正在渲染内容卡...")
    viz = MatchCardGenerator()
    img_path = viz.generate_card(card_data)
    
    print(f"\n✨ 成功! 内容卡已生成至: {img_path}")
    print("💡 备注: 该卡片使用了真实的 2022 决赛事件数据计算出的 xG。")

if __name__ == "__main__":
    main()
