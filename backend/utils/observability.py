"""
可观测性 — Prometheus 指标采集

为 Grafana / 监控系统提供标准化指标。
"""
from __future__ import annotations

import time
import logging

logger = logging.getLogger(__name__)

# ─── 指标定义 ───

# 计数器: 预测总数
_predictions_total = {}
_counter_lock = {}

# 直方图: 预测耗时
_latency_buckets = [0.1, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0]
_latency_counts = {}
_latency_sum = {}
_latency_count = {}

#  Gauge: 当前准确率
_accuracy_gauges = {}

# Gauge: 数据源健康度
_source_health = {}


def inc_predictions(model_version: str, play_type: str, competition: str = ""):
    """增加预测计数器"""
    key = f"{model_version}:{play_type}:{competition}"
    _predictions_total[key] = _predictions_total.get(key, 0) + 1


def start_prediction_timer():
    """开始计时"""
    return time.monotonic()


def observe_prediction_latency(elapsed: float, model_version: str, play_type: str):
    """记录预测耗时"""
    key = f"{model_version}:{play_type}"
    if key not in _latency_counts:
        _latency_counts[key] = {b: 0 for b in _latency_buckets}
        _latency_sum[key] = 0.0
        _latency_count[key] = 0

    _latency_sum[key] += elapsed
    _latency_count[key] += 1
    for b in _latency_buckets:
        if elapsed <= b:
            _latency_counts[key][b] += 1
            break


def set_accuracy(model_version: str, play_type: str, value: float):
    """设置准确率 Gauge"""
    key = f"{model_version}:{play_type}"
    _accuracy_gauges[key] = value


def set_source_health(source: str, healthy: bool):
    """设置数据源健康度"""
    _source_health[source] = 1.0 if healthy else 0.0


def get_prometheus_metrics() -> dict:
    """
    返回 Prometheus 格式的指标字典。
    生产环境可集成 prometheus-fastapi-instrumentator。
    """
    metrics = {}

    # predictions_total
    for key, count in _predictions_total.items():
        mv, pt, comp = key.split(":")
        metrics[f"predictions_total{{model_version=\"{mv}\",play_type=\"{pt}\",competition=\"{comp}\"}}"] = count

    # prediction_latency_sum/count
    for key, vals in _latency_counts.items():
        mv, pt = key.split(":")
        s = _latency_sum[key]
        c = _latency_count[key]
        metrics[f"prediction_latency_sum{{model_version=\"{mv}\",play_type=\"{pt}\"}}"] = s
        metrics[f"prediction_latency_count{{model_version=\"{mv}\",play_type=\"{pt}\"}}"] = c

    # model_accuracy
    for key, val in _accuracy_gauges.items():
        mv, pt = key.split(":")
        metrics[f"model_accuracy{{model_version=\"{mv}\",play_type=\"{pt}\"}}"] = val

    # data_source_health
    for source, healthy in _source_health.items():
        metrics[f"data_source_health{{source=\"{source}\"}}"] = healthy

    return metrics


def metrics_summary() -> str:
    """返回可读的指标摘要"""
    lines = ["=== Observable Metrics ==="]

    total_preds = sum(_predictions_total.values())
    lines.append(f"Total predictions: {total_preds}")

    if _predictions_total:
        lines.append("  By model/play:")
        for key, count in sorted(_predictions_total.items(), key=lambda x: -x[1])[:10]:
            lines.append(f"    {key}: {count}")

    if _accuracy_gauges:
        lines.append("  Accuracy gauges:")
        for key, val in _accuracy_gauges.items():
            lines.append(f"    {key}: {val:.4f}")

    if _source_health:
        lines.append("  Source health:")
        for source, healthy in _source_health.items():
            status = "OK" if healthy else "DOWN"
            lines.append(f"    {source}: {status}")

    return "\n".join(lines)
