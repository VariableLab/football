#!/bin/bash
cd /Users/liuxuran/Github/football/backend
source venv/bin/activate
nohup python3 run_train.py > logs/lr_retrain.$(date +%Y%m%d_%H%M%S).log 2>&1 &
echo "PID: $!"
echo "日志: logs/lr_retrain.*.log"
echo "tail -f logs/lr_retrain.*.log 查看进度"
