"""
backend/utils/paths.py — 全局绝对路径中心

说明：
    本文件集中定义 backend 包运行时所需的全部 *绝对* 资源路径，
    用于替换散落在各模块里的相对路径（如 ``./data/...``）。
    相对路径在 gunicorn 服务、systemd、celery 这些场景下，
    会因 cwd 不同而指向不同目录，引发"线上读不到权重"这类隐性故障。
    本模块强制把路径锚到 backend/data 的真实磁盘位置，使
    dev、test、prod 三种模式行为一致。

A. 位置与目录
    ``BACKEND_DIR``     backend/ 目录的 Path
    ``DATA_DIR``        backend/data/ 目录，存放权重 / sqlite 镜像 / 临时缓存
    ``WEIGHTS_LR_DIR``  backend/data/weights/lr/，LR 融合模型权重目录
    ``RESEARCH_CACHE``  backend/data/research/，研究回流与实验产物
    ``SCRATCH_DIR``     backend/data/scratch/，单次产物的临时目录
    ``LOGS_DIR``        backend/utils/logs/，日志目录

B. 路径工具
    ``safe_join``       安全拼接子路径，防止 ``..`` 跳出
    ``ensure_dir``      目录不存在则创建

C. 兼容映射
    ``legacy_aliases``  兼容旧 cwd 下的 ``./data/...`` 路径：当 BACKEND/data
                        不存在、cwd 下 data 存在时返回 cwd/data，实现向后兼容，
                        但伴随 ``E_LOG`` 警告。

注意：
    - 本模块的副作用限制为 ``ensure_dir``，不会触碰数据库/网络。
    - 该模块必须 *很早就* 被导入（在任何 prediction/fusion 模块前）。
"""
from __future__ import annotations

import os
import warnings
from pathlib import Path
from typing import Union

# ────────────────────────────
# 基础位置
# ────────────────────────────
# __file__ = backend/utils/paths.py → 向上两级即 backend/
BACKEND_DIR: Path = Path(__file__).resolve().parent.parent
DATA_DIR: Path = BACKEND_DIR / "data"
WEIGHTS_LR_DIR: Path = DATA_DIR / "weights" / "lr"
RESEARCH_CACHE: Path = DATA_DIR / "research"
SCRATCH_DIR: Path = DATA_DIR / "scratch"
MODEL_AUDIT_DIR: Path = DATA_DIR / "model_audit"
LOGS_DIR: Path = BACKEND_DIR / "utils" / "logs"


def safe_join(base: Path, *parts: str) -> Path:
    """把 parts 安全拼到 base 下，防止 ``..`` 跳出父目录。"""
    target = base.joinpath(*parts).resolve()
    base_resolved = base.resolve()
    try:
        target.relative_to(base_resolved)
    except ValueError as exc:
        raise ValueError(f"Path traversal blocked: {target} ∉ {base_resolved}") from exc
    return target


def ensure_dir(path: Union[str, Path]) -> Path:
    """目录不存在则创建，返回 Path 对象。"""
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return p


# ────────────────────────────
# 兼容性别名（避免破坏现有调用点）
# ────────────────────────────
def legacy_aliases(*candidates: str) -> Path:
    """
    兼容旧代码里散落的相对路径（如 ``./data/weights/lr``）。

    行为：
    1. 先看显式传入的候选；
    2. 默认指向绝对路径 ``WEIGHTS_LR_DIR``；
    3. 仅当显式候选存在而新目录不存在时，返回旧路径并发 warning。
    """
    if not candidates:
        return WEIGHTS_LR_DIR
    for cand in candidates:
        p = Path(cand)
        if p.is_absolute() and p.exists():
            return p
        # 相对路径按 cwd 解析；仅在显式设置时使用
        if p.exists() and not WEIGHTS_LR_DIR.exists():
            warnings.warn(
                f"legacy path '{cand}' is being used; "
                f"prefer absolute path '{WEIGHTS_LR_DIR}'.",
                RuntimeWarning,
                stacklevel=2,
            )
            return p.resolve()
    return WEIGHTS_LR_DIR


def init_data_dirs() -> None:
    """启动时确保所有运行时目录存在。幂等，多次调用安全。"""
    for d in (DATA_DIR, WEIGHTS_LR_DIR, RESEARCH_CACHE, SCRATCH_DIR, MODEL_AUDIT_DIR, LOGS_DIR):
        ensure_dir(d)


__all__ = [
    "BACKEND_DIR",
    "DATA_DIR",
    "WEIGHTS_LR_DIR",
    "RESEARCH_CACHE",
    "SCRATCH_DIR",
    "MODEL_AUDIT_DIR",
    "LOGS_DIR",
    "safe_join",
    "ensure_dir",
    "legacy_aliases",
    "init_data_dirs",
]
