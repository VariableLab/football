#!/usr/bin/env python3
"""
验证三项改进的执行结果

用法:
    cd backend && source venv/bin/activate && python verify_improvements.py
"""
import os
import sys


from database.models import SessionLocal, Prediction, Team, Match
from utils.logger import get_logger

logger = get_logger("verify_improvements")


def check_confidence_fill_rate():
    """检查 confidence 字段填充率"""
    print("\n" + "=" * 60)
    print("  任务 3: 置信度填充率检查")
    print("=" * 60)

    db = SessionLocal()
    try:
        total = db.query(Prediction).count()
        with_conf = db.query(Prediction).filter(
            Prediction.confidence.isnot(None),
            Prediction.confidence != ""
        ).count()

        # 按 play_type 细分
        from sqlalchemy import func
        by_type = db.query(
            Prediction.play_type,
            func.count(Prediction.id).label('total'),
            func.sum(func.if_(
                Prediction.confidence.isnot(None) & (Prediction.confidence != ""), 1, 0
            )).label('with_conf')
        ).group_by(Prediction.play_type).all()

        print(f"\n  总预测数: {total}")
        print(f"  有 confidence: {with_conf}")
        print(f"  填充率: {with_conf/max(total,1)*100:.1f}%")

        print(f"\n  按玩法类型:")
        for row in by_type:
            total_t = row.total or 0
            conf_t = row.with_conf or 0
            pct = conf_t / max(total_t, 1) * 100
            print(f"    {row.play_type}: {conf_t}/{total_t} = {pct:.1f}%")

        # 检查是否有 None 的 confidence
        none_conf = db.query(Prediction).filter(
            Prediction.confidence.is_(None) | (Prediction.confidence == "")
        ).count()
        if none_conf > 0:
            print(f"\n  ⚠️  警告: {none_conf} 条预测缺少 confidence 字段")
            # 检查这些预测对应的比赛是否有结果
            print("  可能需要手动触发 lock_predictions_job 重新生成")

    finally:
        db.close()


def check_feature_coverage():
    """检查特征数据覆盖率"""
    print("\n" + "=" * 60)
    print("  任务 2: 特征数据覆盖率检查")
    print("=" * 60)

    db = SessionLocal()
    try:
        total = db.query(Team).count()

        print(f"\n  总球队数: {total}")
        print(f"\n  特征字段覆盖率:")

        fields = {
            'avg_xg': '期望进球 (xG)',
            'avg_xga': '期望失球 (xGA)',
            'possession': '控球率',
            'pass_completion': '传球成功率',
            'shots_per_game': '场均射门',
            'rest_days': '休息天数',
            'key_injuries': '核心伤停',
        }

        for field, label in fields.items():
            count = db.query(Team).filter(
                getattr(Team, field).isnot(None),
            ).count()
            if field in ('possession', 'pass_completion', 'shots_per_game'):
                # 数值型字段，排除 0
                count = db.query(Team).filter(
                    getattr(Team, field).isnot(None),
                    getattr(Team, field) != 0
                ).count()
            pct = count / max(total, 1) * 100
            status = "✅" if pct > 80 else "⚠️" if pct > 50 else "❌"
            print(f"    {status} {label} ({field}): {count}/{total} = {pct:.1f}%")

    finally:
        db.close()


def check_validation_meta():
    """检查验证元数据"""
    print("\n" + "=" * 60)
    print("  任务 1: LR 验证集大小检查")
    print("=" * 60)

    import json

    # 检查 train_all.py 中的 val_ratio
    train_all_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "train_all.py")
    with open(train_all_path) as f:
        content = f.read()
        if "val_ratio=0.15" in content:
            print("  ✅ train_all.py: val_ratio = 0.15 (15%)")
        elif "val_ratio=0.1" in content:
            print("  ❌ train_all.py: val_ratio = 0.1 (10%) — 需要修改")
        else:
            print("  ⚠️  train_all.py: 未找到 val_ratio 参数")

    # 检查 validate_deploy.py 中的默认值
    validate_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fusion", "validate_deploy.py")
    with open(validate_path) as f:
        content = f.read()
        if "val_ratio: float = 0.15" in content:
            print("  ✅ validate_deploy.py: 默认 val_ratio = 0.15 (15%)")
        elif "val_ratio: float = 0.1" in content:
            print("  ❌ validate_deploy.py: 默认 val_ratio = 0.1 (10%) — 需要修改")

    # 检查验证元数据
    meta_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "weights", "lr", "validation_meta.json")
    if os.path.exists(meta_path):
        with open(meta_path) as f:
            meta = json.load(f)
        print(f"\n  最新验证元数据:")
        print(f"    部署时间: {meta.get('deployed_at', 'N/A')}")
        print(f"    准确率: {meta.get('accuracy', 'N/A')}")
        print(f"    样本数: {meta.get('sample_count', 'N/A')}")
        print(f"    权重文件: {meta.get('file', 'N/A')}")
    else:
        print(f"\n  ⚠️  验证元数据不存在: {meta_path}")


if __name__ == "__main__":
    print("=" * 60)
    print("  三项改进验证报告")
    print("=" * 60)

    check_validation_meta()
    check_feature_coverage()
    check_confidence_fill_rate()

    print("\n" + "=" * 60)
    print("  验证完成")
    print("=" * 60)
