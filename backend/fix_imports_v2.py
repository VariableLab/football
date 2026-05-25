import os
import re

def fix_file(path):
    with open(path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # 移除之前错误的 namespaced imports 并还原为相对或顶级 import
    # 由于我们现在有了 package 结构，最好的方式是使用相对 import 或 修正后的绝对 import
    
    # 1. 尝试统一为顶级绝对 import (假设 backend 是 root)
    # 但这需要运行 python 时指定 backend 为 root
    
    # 2. 简单方案：在 main.py 中注入 sys.path，并将内部 import 恢复为 flat
    # 这样最小化修改且最稳健。
    
    # 还原mappings
    reverse_mappings = {
        'from models import': 'from models import',
        'from config import': 'from config import',
        'from schemas import': 'from schemas import',
        'from auth import': 'from auth import',
        'from logger import': 'from logger import',
        'from strategy_pipeline import': 'from strategy_pipeline import',
        'from position_sizer import': 'from position_sizer import',
        'from risk_manager import': 'from risk_manager import',
        'from edge_calculator import': 'from edge_calculator import',
        'from optimal_combo import': 'from optimal_combo import',
        'from prediction_engine import': 'from prediction_engine import',
        'from residual_nn import': 'from residual_nn import',
        'from sporttery_sync import': 'from sporttery_sync import',
        'from jingcai_quant_collector import': 'from jingcai_quant_collector import',
        'from odds_collector import': 'from odds_collector import',
        'from odds_tracker import': 'from odds_tracker import',
    }

    new_content = content
    for old, new in reverse_mappings.items():
        new_content = new_content.replace(old, new)
    
    if new_content != content:
        with open(path, 'w', encoding='utf-8') as f:
            f.write(new_content)
        print(f"Reverted {path}")

for root, dirs, files in os.walk('.'):
    if 'venv' in root or '.git' in root: continue
    for f in files:
        if f.endswith('.py'):
            fix_file(os.path.join(root, f))
