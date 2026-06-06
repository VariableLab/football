import pandas as pd
import os
import requests
from typing import List

class FootballDataLoader:
    """
    负责从 football-data.co.uk 加载数据。
    """
    BASE_URL = "https://www.football-data.co.uk/mmz4281"
    
    def __init__(self, raw_dir: str):
        self.raw_dir = raw_dir
        os.makedirs(raw_dir, exist_ok=True)
        
    def download_season(self, league: str, season: str):
        """
        下载特定赛季的数据。
        :param league: 联赛代码 (例如 'E0' 为英超)
        :param season: 赛季代码 (例如 '2324' 为 2023/24)
        """
        filename = f"{league}_{season}.csv"
        target_path = os.path.join(self.raw_dir, filename)
        
        if os.path.exists(target_path):
            return target_path
            
        url = f"{self.BASE_URL}/{season}/{league}.csv"
        print(f"Downloading {url}...")
        response = requests.get(url)
        response.raise_for_status()
        
        with open(target_path, 'wb') as f:
            f.write(response.content)
        return target_path

    def load_processed(self, files: List[str]) -> pd.DataFrame:
        """
        加载并清洗多个 CSV 文件。
        """
        dfs = []
        for f in files:
            path = os.path.join(self.raw_dir, f)
            df = pd.read_csv(path)
            # 统一列名和格式
            # 核心列: Date, HomeTeam, AwayTeam, FTHG, FTAG, FTR (Full Time Result)
            cols = ['Date', 'HomeTeam', 'AwayTeam', 'FTHG', 'FTAG', 'FTR']
            # 某些旧赛季可能列名略有不同，这里做基础兼容
            df = df[cols].copy()
            df['Date'] = pd.to_datetime(df['Date'], dayfirst=True)
            dfs.append(df)
            
        full_df = pd.concat(dfs, ignore_index=True)
        full_df = full_df.sort_values('Date')
        return full_df
