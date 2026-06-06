import pandas as pd
import os
import requests

class KaggleInternationalLoader:
    """
    加载 Kaggle/GitHub 上的国际足球比赛结果数据集 (1872-至今)。
    数据源: https://github.com/martj42/international_results
    """
    RESULTS_URL = "https://raw.githubusercontent.com/martj42/international_results/master/results.csv"
    SHOOTOUTS_URL = "https://raw.githubusercontent.com/martj42/international_results/master/shootouts.csv"
    
    def __init__(self, raw_dir: str):
        self.raw_dir = raw_dir
        os.makedirs(raw_dir, exist_ok=True)

    def download_data(self):
        """下载最新的国际比赛结果 CSV"""
        for url in [self.RESULTS_URL, self.SHOOTOUTS_URL]:
            filename = url.split('/')[-1]
            target_path = os.path.join(self.raw_dir, filename)
            
            print(f"📥 正在从 GitHub 镜像下载 {filename}...")
            response = requests.get(url)
            response.raise_for_status()
            
            with open(target_path, 'wb') as f:
                f.write(response.content)
            print(f"✅ 已保存至 {target_path}")

    def load_results(self) -> pd.DataFrame:
        """加载并预处理比赛结果"""
        path = os.path.join(self.raw_dir, "results.csv")
        if not os.path.exists(path):
            self.download_data()
            
        df = pd.read_csv(path)
        df['date'] = pd.to_datetime(df['date'])
        return df.sort_values('date')
