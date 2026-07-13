"""
多项式逻辑回归融合器 — LogisticFusion

替代旧的 4 参数线性加权 (EnsembleFusion)，使用 L-BFGS-B 优化
多项式逻辑回归的 cross-entropy loss + L1 正则化。

数学:
  log(P_home / P_draw) = beta_home · X
  log(P_away / P_draw) = beta_away · X
  P = softmax([logodds_h, 0, logodds_a])

特性:
  - 自然输出校准概率
  - L1 正则化自动特征选择
  - 系数即特征贡献，完全可解释
  - 支持联赛分层训练
  - 兼容 scipy.optimize.minimize (已有依赖)
"""
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

import numpy as np
from scipy.optimize import minimize
from scipy.special import softmax

from utils.logger import get_logger
from utils.paths import WEIGHTS_LR_DIR, ensure_dir

logger = get_logger("logistic_fusion")

# 权重存储目录：使用绝对路径，避免 gunicorn/systemd/celery
# 因 cwd 不同而读到旧文件。兼容向导：若绝对目录不存在但老的相对目录存在，
# 自动迁移目录内容后才使用绝对目录。
WEIGHTS_DIR = ensure_dir(WEIGHTS_LR_DIR).as_posix()

# 特征名称 — 单一真相源 features.schema（48 基线 + 5 交互 = 53）
from features.schema import BASE_FEATURE_DIM, FEATURE_NAMES, FULL_FEATURE_DIM  # noqa: E402



@dataclass
class LogisticFusionWeights:
    """逻辑回归融合权重"""
    coef_home: np.ndarray          # (D,) 主胜 log-odds 系数
    coef_away: np.ndarray          # (D,) 客胜 log-odds 系数
    intercept_home: float = 0.0
    intercept_away: float = 0.0
    l1_penalty: float = 0.001
    input_dim: int = BASE_FEATURE_DIM

    # 元信息
    league: str = "global"
    trained_at: str = ""
    sample_count: int = 0
    cross_entropy: float = 0.0
    accuracy: float = 0.0

    def predict(self, features: np.ndarray) -> Dict[str, float]:
        """
        推理：特征 → 概率。

        Args:
            features: shape (D,) 或 (N, D)

        Returns:
            {"home": float, "draw": float, "away": float}
            或批量时返回三个等长数组
        """
        if features.ndim == 1:
            features = features.reshape(1, -1)
            
        # 维度检查与自适应处理
        incoming_dim = features.shape[1]
        weight_dim = self.coef_home.shape[0]
        
        if incoming_dim != weight_dim:
            if incoming_dim > weight_dim:
                # 💡 兼容性：如果输入包含多余特征（如新加的交互项），自动截断以匹配旧权重
                logger.debug(f"[logistic_fusion] Truncating features: {incoming_dim} -> {weight_dim}")
                features = features[:, :weight_dim]
            else:
                raise ValueError(
                    f"Feature dimension mismatch: expected {weight_dim}, "
                    f"got {incoming_dim}. Model needs retraining."
                )

        logodds_home = features @ self.coef_home + self.intercept_home
        logodds_away = features @ self.coef_away + self.intercept_away

        # 构造 (N, 3) log-odds，draw 始终为 0
        logodds = np.column_stack([
            logodds_home,
            np.zeros_like(logodds_home),
            logodds_away,
        ])

        probs = softmax(logodds, axis=1)

        if probs.shape[0] == 1:
            return {
                "home": float(probs[0, 0]),
                "draw": float(probs[0, 1]),
                "away": float(probs[0, 2]),
            }
        return probs

    def explain(self, features: np.ndarray) -> List[Dict]:
        """返回每个特征对 home/away 概率的贡献"""
        if features.ndim != 1:
            features = features.flatten()

        explanations = []
        for i, name in enumerate(FEATURE_NAMES[:len(self.coef_home)]):
            explanations.append({
                "feature": name,
                "value": round(float(features[i]), 4),
                "coef_home": round(float(self.coef_home[i]), 4),
                "coef_away": round(float(self.coef_away[i]), 4),
                "contrib_home": round(float(self.coef_home[i] * features[i]), 4),
                "contrib_away": round(float(self.coef_away[i] * features[i]), 4),
            })
        return explanations

    def save(self, filename: Optional[str] = None) -> str:
        """保存权重到 JSON"""
        if filename is None:
            filename = f"{self.league}_v1_{self.trained_at[:10]}.json"
        path = os.path.join(WEIGHTS_DIR, filename)
        data = {
            "coef_home": self.coef_home.tolist(),
            "coef_away": self.coef_away.tolist(),
            "intercept_home": self.intercept_home,
            "intercept_away": self.intercept_away,
            "l1_penalty": self.l1_penalty,
            "input_dim": self.input_dim,
            "league": self.league,
            "trained_at": self.trained_at,
            "sample_count": self.sample_count,
            "cross_entropy": self.cross_entropy,
            "accuracy": self.accuracy,
        }
        with open(path, "w") as f:
            json.dump(data, f, indent=2)
        logger.info(f"[logistic_fusion] Weights saved to {path}")
        return path

    @classmethod
    def load(cls, filepath: str) -> "LogisticFusionWeights":
        """从 JSON 加载权重"""
        with open(filepath, "r") as f:
            data = json.load(f)
        return cls(
            coef_home=np.array(data["coef_home"], dtype=np.float64),
            coef_away=np.array(data["coef_away"], dtype=np.float64),
            intercept_home=data.get("intercept_home", 0.0),
            intercept_away=data.get("intercept_away", 0.0),
            l1_penalty=data.get("l1_penalty", 0.001),
            input_dim=data.get("input_dim", 48),
            league=data.get("league", "global"),
            trained_at=data.get("trained_at", ""),
            sample_count=data.get("sample_count", 0),
            cross_entropy=data.get("cross_entropy", 0.0),
            accuracy=data.get("accuracy", 0.0),
        )


class LogisticFusionTrainer:
    """
    逻辑回归融合训练器。

    优化: L-BFGS-B 最小化 cross-entropy + L1 penalty
    """

    def __init__(
        self,
        l1_penalty: float = 0.001,
        max_iter: int = 1000,
        verbose: bool = False,
        class_weight: Optional[Dict[int, float]] = None,
    ):
        # 确保 L1 正则化永不归零 —— 这是 P0 修复的关键防线
        self.l1_penalty = max(0.001, l1_penalty)
        self.max_iter = max_iter
        self.verbose = verbose
        self.class_weight = class_weight

    def fit(
        self,
        X: np.ndarray,
        y: np.ndarray,
        league: str = "global",
    ) -> LogisticFusionWeights:
        """
        训练逻辑回归融合器。

        Args:
            X: shape (N, D) 特征矩阵
            y: shape (N,) 标签，0=home, 1=draw, 2=away
            league: 联赛标签（用于保存时标记）

        Returns:
            LogisticFusionWeights
        """
        N, D = X.shape
        logger.info(f"[logistic_fusion] Training on {N} samples, {D} features, l1={self.l1_penalty}")

        # 初始化参数: [beta_home (D), beta_away (D), intercept_home, intercept_away]
        init_params = np.zeros(2 * D + 2)

        # L-BFGS-B 优化
        result = minimize(
            fun=self._loss_and_grad,
            x0=init_params,
            args=(X, y),
            method="L-BFGS-B",
            jac=True,
            options={"maxiter": self.max_iter, "disp": self.verbose},
        )

        # 解析参数
        beta_home = result.x[:D]
        beta_away = result.x[D:2*D]
        intercept_home = result.x[2*D]
        intercept_away = result.x[2*D + 1]

        # 评估
        weights = LogisticFusionWeights(
            coef_home=beta_home,
            coef_away=beta_away,
            intercept_home=intercept_home,
            intercept_away=intercept_away,
            l1_penalty=self.l1_penalty,
            input_dim=D,
            league=league,
            trained_at=datetime.now(timezone.utc).isoformat(),
            sample_count=N,
            cross_entropy=float(result.fun),
        )

        # 计算准确率
        probs = weights.predict(X)
        if isinstance(probs, np.ndarray):
            preds = np.argmax(probs, axis=1)
            weights.accuracy = float(np.mean(preds == y))

        # 打印特征重要性
        self._log_feature_importance(weights)

        return weights

    def _loss_and_grad(
        self,
        params: np.ndarray,
        X: np.ndarray,
        y: np.ndarray,
    ) -> Tuple[float, np.ndarray]:
        """
        计算 cross-entropy loss + L1 penalty 及其梯度。

        params = [beta_home (D), beta_away (D), intercept_home, intercept_away]
        """
        N, D = X.shape
        beta_home = params[:D]
        beta_away = params[D:2*D]
        intercept_home = params[2*D]
        intercept_away = params[2*D + 1]

        # log-odds
        logodds_home = X @ beta_home + intercept_home
        logodds_away = X @ beta_away + intercept_away
        logodds_draw = np.zeros(N)

        # softmax
        logodds = np.column_stack([logodds_home, logodds_draw, logodds_away])
        logodds_max = np.max(logodds, axis=1, keepdims=True)
        exp_shifted = np.exp(logodds - logodds_max)
        probs = exp_shifted / np.sum(exp_shifted, axis=1, keepdims=True)

        # cross-entropy loss (with optional class_weight)
        y_onehot = np.zeros((N, 3))
        y_onehot[np.arange(N), y] = 1
        eps = 1e-15
        if self.class_weight:
            sample_weights = np.array([self.class_weight.get(int(yi), 1.0) for yi in y], dtype=np.float64)
            ce_per_sample = -np.sum(y_onehot * np.log(probs + eps), axis=1)
            ce_loss = np.mean(sample_weights * ce_per_sample)
        else:
            ce_loss = -np.mean(np.sum(y_onehot * np.log(probs + eps), axis=1))

        # L1 penalty
        l1_pen = self.l1_penalty * (np.sum(np.abs(beta_home)) + np.sum(np.abs(beta_away)))

        loss = ce_loss + l1_pen

        # ─── 梯度 ───
        # dL/d(logodds) = probs - y_onehot (weighted if class_weight set)
        if self.class_weight:
            sample_weights = np.array([self.class_weight.get(int(yi), 1.0) for yi in y], dtype=np.float64)
            grad_logodds = (probs - y_onehot) * sample_weights[:, np.newaxis] / N
        else:
            grad_logodds = (probs - y_onehot) / N  # (N, 3)

        # beta_home 梯度 (第0列) + L1 subgradient
        grad_beta_home = X.T @ grad_logodds[:, 0] + self.l1_penalty * np.sign(beta_home)
        # beta_away 梯度 (第2列) + L1 subgradient
        grad_beta_away = X.T @ grad_logodds[:, 2] + self.l1_penalty * np.sign(beta_away)
        # intercept 梯度
        grad_intercept_home = np.sum(grad_logodds[:, 0])
        grad_intercept_away = np.sum(grad_logodds[:, 2])

        grad = np.concatenate([grad_beta_home, grad_beta_away,
                               [grad_intercept_home, grad_intercept_away]])

        return loss, grad

    @staticmethod
    def _log_feature_importance(weights: LogisticFusionWeights) -> None:
        """打印 Top 10 最重要特征"""
        importance_home = np.abs(weights.coef_home)
        importance_away = np.abs(weights.coef_away)
        importance = importance_home + importance_away

        top_idx = np.argsort(-importance)[:10]
        lines = []
        for i in top_idx:
            name = FEATURE_NAMES[i] if i < len(FEATURE_NAMES) else f"feat_{i}"
            lines.append(
                f"  {name:25s}  home={weights.coef_home[i]:+.4f}  away={weights.coef_away[i]:+.4f}"
            )
        logger.info("[logistic_fusion] Top 10 features:\n" + "\n".join(lines))


def _purged_time_folds(n: int, n_folds: int, embargo: int, time_index: Optional[np.ndarray] = None):
    """
    生成按时间顺序的 walk-forward K-fold 索引(2026-06-25 整改新增)。

    设计:
      - 严格 walk-forward:train 只能看到 val **之前**的历史,val 不能进 train。
      - 折区间留 ``embargo`` 条样本的 purge gap,防止相邻比赛日期接近导致的特征泄漏
        (一个联赛同一天的多场比赛,其滚动特征如 Elo/近况自然会跨场传染)。
      - 若提供 ``time_index``,先按时间戳升序排序后再切,保证 train 永远早于 val;

    Args:
        n: 样本数
        n_folds: 折数
        embargo: 折间 purge gap(以排序后的样本下标为单位)
        time_index: 可选时间戳数组。提供时按时间戳排序后切;
                    不提供时按当前下标顺序切(也不随机打散)。

    Yields:
        (train_idx, val_idx) — train.max() < val.min() (严格 time-monotonic)。
    """
    if time_index is not None and len(time_index) == n:
        order = np.argsort(time_index, kind="mergesort")
    else:
        order = np.arange(n)
    n = len(order)

    sizes = _walk_forward_split_sizes(n, n_folds, embargo)

    for (train_end, val_end) in sizes:
        if train_end <= 0:
            # 折叠 0: train 为空,跳过 val 评估(由调用方 hold-out 兜底)
            yield order[np.arange(0, 0)].astype(int), order[np.arange(train_end + embargo, val_end)].astype(int)
            continue
        train_pos = np.arange(0, train_end)
        val_pos = np.arange(train_end + embargo, val_end)
        yield order[train_pos].astype(int), order[val_pos].astype(int)
        if val_end >= n:
            return


def _walk_forward_split_sizes(n: int, n_folds: int, embargo: int) -> List[Tuple[int, int]]:
    """
    规划 expanding-window walk-forward 切分边界,返回 ``[(train_end, gap_end), ...]``:
      - ``train_end``: 本折训练集终止下标(不含)
      - ``gap_end``:   本折验证集终止下标(不含)

    设计要点 (2026-06-25):
      - **expanding train + fixed-size stride**: 训练集在所有后续折叠中继续增长,
        保证每折训练数据包含此前全部历史观测(无未来信息泄漏)。
      - step = ``floor(n / (n_folds + 1))`` 留出至少一折 val + embargo 的尾部预算,
        避免最后折叠 val 越界。
      - 最末折叠 val_end 自适应截到 ``n``, 完全避免越界访问。
      - 当 ``n_folds * step + embargo >= n`` 时折叠数自动收紧至代数可行边界。
    """
    if n_folds <= 0 or n <= 0:
        return []
    # step 越小 train 越多、val 越短; floor(n/(n_folds+1)) 留出至少一折 val 空间
    step = max(1, n // (n_folds + 1))
    out: List[Tuple[int, int]] = []
    cursor = 0
    for fold in range(n_folds):
        train_end = cursor + step
        if train_end > n:
            break
        val_start = train_end + embargo
        val_end = val_start + step
        if val_end > n:
            val_end = n
        if val_start >= val_end:
            # 当前折叠没有可用 val (样本尾部不足), 后续更不会有
            break
        out.append((train_end, val_end))
        cursor = train_end  # 下一折 train 起点 = 当前 train 终点
        if val_end >= n:
            break
    return out


def cross_validate_lambda(
    X: np.ndarray,
    y: np.ndarray,
    lambdas: List[float] = None,
    n_folds: int = 5,
    class_weight: Optional[Dict[int, float]] = None,
    time_index: Optional[np.ndarray] = None,
    embargo: int = 50,
) -> Tuple[float, Dict[float, float]]:
    """
    交叉验证选择最优 L1 正则化强度。

    关键改进 (2026-06-25):
      - 默认使用 purged 时序 K-fold：每折训练集只由先前样本组成，
        验证集只由后续样本组成，模拟真实推断的"未来不可见"约束。
      - 折间使用 ``embargo`` 个样本 gap 防止近期信息泄漏（金融时序常用做法）。
      - 若调用方传入 ``time_index``，按时间戳排序后再分；否则按当前顺序
        分（不再随机打散），由调用方负责保证顺序合理。

    Args:
        X: (N, D) 特征矩阵
        y: (N,) 标签 (0=home, 1=draw, 2=away)
        lambdas: L1 正则强度候选
        n_folds: 折数
        class_weight: 类别权重字典
        time_index: 可选时间戳向量，长度 N。None 表示调用方已按时间排好。
        embargo: 分位间 embargo（样本数），默认 50。

    Returns:
        (best_lambda, {lambda: avg_accuracy})
    """
    if lambdas is None:
        # P0 修复: 强制至少 0.001 的 L1 正则化
        lambdas = [0.001, 0.005, 0.01, 0.02, 0.05, 0.1]

    results: Dict[float, float] = {}
    best_lambda = lambdas[0]
    best_acc = -1.0

    folds = list(_purged_time_folds(X.shape[0], n_folds, embargo=embargo, time_index=time_index))

    for lam in lambdas:
        accs = []
        for train_idx, val_idx in folds:
            if len(train_idx) < 10 or len(val_idx) < 5:
                continue

            X_train, y_train = X[train_idx], y[train_idx]
            X_val, y_val = X[val_idx], y[val_idx]

            trainer = LogisticFusionTrainer(l1_penalty=lam, max_iter=500, class_weight=class_weight)
            weights = trainer.fit(X_train, y_train)

            probs = weights.predict(X_val)
            if isinstance(probs, np.ndarray):
                preds = np.argmax(probs, axis=1)
                accs.append(float(np.mean(preds == y_val)))

        avg_acc = float(np.mean(accs)) if accs else 0.0
        results[lam] = avg_acc
        if avg_acc > best_acc:
            best_acc = avg_acc
            best_lambda = lam

    return best_lambda, results
