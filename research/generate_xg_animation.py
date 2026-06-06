import matplotlib.pyplot as plt
import matplotlib.animation as animation
import pandas as pd
import numpy as np
import os
from statsbombpy import sb

class DynamicContentGenerator:
    """
    动态内容生成器。
    生成 xG 随时间演化的动态线图。
    """
    def __init__(self, output_dir="research/reports/animations"):
        self.output_dir = output_dir
        os.makedirs(output_dir, exist_ok=True)

    def generate_xg_race(self, match_id: int, home_team: str, away_team: str):
        """
        生成一场比赛的 xG 动态演化图 (xG Race Chart)。
        """
        print(f"Fetching events for match {match_id}...")
        events = sb.events(match_id=match_id)
        
        # 筛选射门事件并提取 minute, second, team, xg
        shots = events[events['type'] == 'Shot'].copy()
        shots['timestamp_sec'] = shots['minute'] * 60 + shots['second']
        shots = shots.sort_values('timestamp_sec')

        # 计算累计 xG
        times = [0]
        h_xg = [0.0]
        a_xg = [0.0]
        
        curr_h, curr_a = 0.0, 0.0
        
        for _, shot in shots.iterrows():
            times.append(shot['timestamp_sec'] / 60.0) # 转为分钟
            if shot['team'] == home_team:
                curr_h += shot['shot_statsbomb_xg']
            else:
                curr_a += shot['shot_statsbomb_xg']
            h_xg.append(curr_h)
            a_xg.append(curr_a)
            
        # 添加终场时间
        max_min = max(shots['minute'].max(), 90)
        times.append(max_min)
        h_xg.append(curr_h)
        a_xg.append(curr_a)

        # 设置绘图环境
        fig, ax = plt.subplots(figsize=(10, 6), facecolor='#001F3F')
        ax.set_facecolor('#001F3F')
        
        line_h, = ax.plot([], [], color='#0074D9', lw=3, label=f"{home_team} xG")
        line_a, = ax.plot([], [], color='#FF4136', lw=3, label=f"{away_team} xG")
        
        ax.set_xlim(0, max_min + 5)
        ax.set_ylim(0, max(max(h_xg), max(a_xg)) + 0.5)
        
        ax.set_xlabel("Match Minute", color='white')
        ax.set_ylabel("Cumulative Expected Goals (xG)", color='white')
        ax.set_title(f"xG RACE: {home_team} vs {away_team}", color='#FFCC00', fontsize=16, fontweight='bold')
        
        ax.tick_params(colors='white')
        ax.legend(loc='upper left', frameon=False, labelcolor='white')
        ax.grid(color='#333333', linestyle='--', alpha=0.5)

        def init():
            line_h.set_data([], [])
            line_a.set_data([], [])
            return line_h, line_a

        def animate(i):
            line_h.set_data(times[:i], h_xg[:i])
            line_a.set_data(times[:i], a_xg[:i])
            return line_h, line_a

        print("Rendering animation...")
        # 降低帧数以加快演示速度
        ani = animation.FuncAnimation(fig, animate, init_func=init, frames=len(times), interval=100, blit=True)
        
        output_path = os.path.join(self.output_dir, f"xg_race_{match_id}.gif")
        # 需要安装 pillow: pip install pillow
        ani.save(output_path, writer='pillow')
        plt.close()
        return output_path

if __name__ == "__main__":
    # 测试: 2022 决赛
    generator = DynamicContentGenerator()
    path = generator.generate_xg_race(3869685, "Argentina", "France")
    print(f"Animation saved to: {path}")
