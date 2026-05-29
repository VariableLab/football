"""
把回测学到的最优权重写入 fusion_weights 表，供生产环境使用。

基于 5330 场联赛 walk-forward 校准:
- group/all: 联赛场景，有真实赔率，market 权重最高
- knockout/all: 世界杯场景，无赔率，Elo+Poisson 为主
- all/all: 全局折中权重
"""

from sqlalchemy.orm import Session
from database.models import SessionLocal, FusionWeight
from prediction_engine import DEFAULT_WEIGHTS


def save_weights(db: Session, weights: dict, stage: str = "all",
                 elo_diff_range: str = "all", metric_value: float = 0,
                 sample_size: int = 0):
    # 标记同维度旧权重为 inactive
    db.query(FusionWeight).filter(
        FusionWeight.stage == stage,
        FusionWeight.elo_diff_range == elo_diff_range,
    ).update({"is_active": False}, synchronize_session=False)

    fw = FusionWeight(
        stage=stage,
        elo_diff_range=elo_diff_range,
        weights=weights,
        metric="brier",
        metric_value=metric_value,
        sample_size=sample_size,
        is_active=True,
    )
    db.add(fw)
    db.commit()
    print(f"Saved weights for {stage}/{elo_diff_range}: {weights} (Brier={metric_value:.4f})")


if __name__ == "__main__":
    db = SessionLocal()
    try:
        # 联赛场景（有真实赔率）
        save_weights(
            db,
            {"elo": 0.00, "poisson": 0.13, "players": 0.20, "market": 0.67},
            "group", "all",
            metric_value=0.1924, sample_size=5330,
        )

        # 世界杯场景（无/少赔率）
        save_weights(
            db,
            {"elo": 0.30, "poisson": 0.21, "players": 0.15, "market": 0.34},
            "knockout", "all",
            metric_value=0.2030, sample_size=5330,
        )

        # 全局折中
        save_weights(
            db,
            {"elo": 0.20, "poisson": 0.25, "players": 0.15, "market": 0.40},
            "all", "all",
            metric_value=0.1945, sample_size=5330,
        )

        # 验证
        for fw in db.query(FusionWeight).filter(FusionWeight.is_active == True).all():
            print(f"  {fw.stage}/{fw.elo_diff_range}: {fw.weights} (n={fw.sample_size})")

        print("Done.")
    finally:
        db.close()
