from statsbombpy import sb
import pandas as pd

class StatsBombLoader:
    """
    StatsBomb 开放数据加载器。
    用于获取世界杯历史比赛的细粒度事件数据（xG, 传球网络等）。
    """
    
    @staticmethod
    def get_world_cups():
        """获取所有可用的世界杯赛季"""
        competitions = sb.competitions()
        return competitions[competitions['competition_name'] == 'FIFA World Cup']
    
    @staticmethod
    def get_matches(competition_id: int, season_id: int):
        """获取特定世界杯赛季的所有比赛"""
        return sb.matches(competition_id=competition_id, season_id=season_id)
    
    @staticmethod
    def get_events(match_id: int):
        """获取某场比赛的所有事件"""
        return sb.events(match_id=match_id)

    def get_match_xg(self, match_id: int):
        """
        计算某场比赛两队的 xG (预期进球)。
        """
        events = self.get_events(match_id)
        # 筛选射门事件
        shots = events[events['type'] == 'Shot'].copy()
        
        # StatsBomb 数据中自带 shot_statsbomb_xg
        if 'shot_statsbomb_xg' in shots.columns:
            xg_summary = shots.groupby('team')['shot_statsbomb_xg'].sum().to_dict()
            return xg_summary
        return {}
