import os
import re

mappings = {
    r'from models import': 'from models import',
    r'from config import': 'from config import',
    r'from schemas import': 'from schemas import',
    r'from auth import': 'from auth import',
    r'from logger import': 'from logger import',
    r'from strategy_pipeline import': 'from strategy_pipeline import',
    r'from position_sizer import': 'from position_sizer import',
    r'from risk_manager import': 'from risk_manager import',
    r'from edge_calculator import': 'from edge_calculator import',
    r'from optimal_combo import': 'from optimal_combo import',
    r'from prediction_engine import': 'from prediction_engine import',
    r'from residual_nn import': 'from residual_nn import',
    r'from sporttery_sync import': 'from sporttery_sync import',
    r'from jingcai_quant_collector import': 'from jingcai_quant_collector import',
    r'from odds_collector import': 'from odds_collector import',
    r'from odds_tracker import': 'from odds_tracker import',
}

def fix_file(path):
    with open(path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    new_content = content
    for old, new in mappings.items():
        new_content = re.sub(old, new, new_content)
    
    if new_content != content:
        with open(path, 'w', encoding='utf-8') as f:
            f.write(new_content)
        print(f"Fixed {path}")

for root, dirs, files in os.walk('.'):
    if 'venv' in root or '.git' in root or '__pycache__' in root:
        continue
    for f in files:
        if f.endswith('.py') and f != 'fix_imports.py':
            fix_file(os.path.join(root, f))
