import matplotlib.pyplot as plt
import matplotlib.patches as patches
import os
import numpy as np
from mplsoccer import Pitch, VerticalPitch

class MatchCardGenerator:
    """
    自动化赛事卡片生成器 (v3 - 极简直观版)。
    特点：
    1. 战术区域化：将模糊的热图改为直观的“高危战区” (Danger Zones)
    2. 进攻箭头：用路径箭头展示球队惯用攻击方向
    3. 战力五角星：直观对比核心素质
    """
    def __init__(self, output_dir: str = "research/reports/cards"):
        self.output_dir = output_dir
        os.makedirs(output_dir, exist_ok=True)

    def generate_pro_card(self, data: dict):
        """
        生成“秒懂级”高价值前瞻卡。
        """
        match_pairing = data['match_info']['pairing']
        home, away = match_pairing.split(' vs ')
        
        # 风格设置
        plt.rcParams['font.family'] = 'sans-serif'
        fig = plt.figure(figsize=(14, 9), facecolor='#0B0F19') # 极简钛金黑
        
        # 1. 顶部：对阵大标题
        plt.text(0.5, 0.94, f"{home.upper()} vs {away.upper()}", color='white', 
                 fontsize=32, fontweight='bold', ha='center', transform=fig.transFigure)
        plt.text(0.5, 0.90, "2026 WORLD CUP TACTICAL SIMULATION", color='#00D4FF', 
                 fontsize=14, fontweight='bold', ha='center', transform=fig.transFigure)

        # 2. 中间左侧：直观战术球场 (Vertical Pitch)
        ax_pitch = fig.add_axes([0.05, 0.15, 0.4, 0.7])
        pitch = VerticalPitch(pitch_type='statsbomb', pitch_color='#111827', line_color='#374151', goal_type='box')
        pitch.draw(ax=ax_pitch)
        
        # --- 这里的改进：不再用模糊的云图，改用“战区高亮”和“进攻路径” ---
        # 模拟高危区 (Danger Zones)
        rect = patches.Rectangle((10, 80), 60, 20, linewidth=2, edgecolor='#FF4136', facecolor='#FF4136', alpha=0.2)
        ax_pitch.add_patch(rect)
        plt.text(40, 90, "HIGH DANGER ZONE", color='#FF4136', fontsize=10, fontweight='bold', ha='center', transform=ax_pitch.transData)
        
        # 绘制主队进攻路径 (箭头)
        pitch.arrows(20, 20, 20, 85, width=3, headwidth=5, headlength=5, color='#00D4FF', ax=ax_pitch, label=f'{home} Attack')
        pitch.arrows(60, 20, 60, 85, width=3, headwidth=5, headlength=5, color='#00D4FF', ax=ax_pitch)
        
        # 3. 中间右侧：战力对比 (Radar/Bar)
        ax_data = fig.add_axes([0.5, 0.2, 0.45, 0.6])
        ax_data.axis('off')
        
        # 战力指标对比
        metrics = ['ATTACK', 'DEFENSE', 'POSSESSION', 'COUNTER', 'STAMINA']
        home_vals = [0.85, 0.72, 0.91, 0.65, 0.78]
        away_vals = [0.75, 0.88, 0.62, 0.95, 0.82]
        
        y_pos = np.arange(len(metrics)) * 1.2
        ax_data.barh(y_pos + 0.2, home_vals, height=0.3, color='#00D4FF', label=home)
        ax_data.barh(y_pos - 0.2, away_vals, height=0.3, color='#FF4136', label=away)
        
        for i, m in enumerate(metrics):
            ax_data.text(-0.05, y_pos[i], m, color='white', fontsize=12, fontweight='bold', ha='right', va='center')
        
        # 4. 底部：核心胜率 (大数字展示)
        p_h, p_d, p_a = data['prediction_ref']['home_win'], data['prediction_ref']['draw'], data['prediction_ref']['away_win']
        
        # 胜率大刻度
        plt.text(0.55, 0.15, f"{home}: {p_h:.0%}", color='#00D4FF', fontsize=24, fontweight='bold', transform=fig.transFigure)
        plt.text(0.72, 0.15, f"DRAW: {p_d:.0%}", color='#AAAAAA', fontsize=20, fontweight='bold', transform=fig.transFigure)
        plt.text(0.85, 0.15, f"{away}: {p_a:.0%}", color='#FF4136', fontsize=24, fontweight='bold', transform=fig.transFigure)

        # 5. AI 金句总结
        plt.text(0.5, 0.05, f"MATCH KEY: {data['ai_analysis']['content'][:80]}...", 
                 color='#00D4FF', fontsize=12, style='italic', ha='center', transform=fig.transFigure, bbox=dict(facecolor='#111827', alpha=0.5, edgecolor='none'))

        # 保存
        filename = f"PRO_PREVIEW_{match_pairing.replace(' ', '_')}.png"
        path = os.path.join(self.output_dir, filename)
        plt.savefig(path, dpi=110, bbox_inches='tight', facecolor='#0B0F19')
        plt.close()
        return path
