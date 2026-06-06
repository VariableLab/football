import requests
import os

class FootballDataOrgLoader:
    """
    football-data.org API 加载器。
    用于获取世界杯赛程、实时比分和积分榜。
    """
    BASE_URL = "https://api.football-data.org/v4"
    
    def __init__(self, api_key: str = None):
        self.api_key = api_key or os.getenv("FOOTBALL_DATA_API_KEY")
        self.headers = {"X-Auth-Token": self.api_key} if self.api_key else {}

    def get_wc_matches(self):
        """获取 2026 世界杯赛程 (WC 代码通常为 WC)"""
        url = f"{self.BASE_URL}/competitions/WC/matches"
        response = requests.get(url, headers=self.headers)
        if response.status_code == 200:
            return response.json().get('matches', [])
        return []

    def get_wc_standings(self):
        """获取世界杯小组赛积分榜"""
        url = f"{self.BASE_URL}/competitions/WC/standings"
        response = requests.get(url, headers=self.headers)
        if response.status_code == 200:
            return response.json().get('standings', [])
        return []
