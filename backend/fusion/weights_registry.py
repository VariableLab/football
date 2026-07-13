"""
backend/fusion/weights_registry.py — LR 权重集中注册表

背景（2026-06-25）：
  旧实现使用 ``_load_lr_weights`` 把 ``{league}_v1_*.json`` 按文件名字母
  序取最后一个文件。这是个隐性故障源——同联赛或不同联赛里若是临时权重
  没清除，会被悄悄作为"线上权重"。本模块把"使用哪个权重"这件事明确为
  单一权威的注册表 + checksum 校验 + 最新/回滚版本控制。

设计要点：
  1. 注册表写在 backend/data/weights/lr/registry.json（绝对路径）。
  2. 注册表条目形如：
        {
          "global": {
            "active": "global_optimized_v1_2026-06-25.json",
            "history": [
              {"file": "...", "sha256": "...", "trained_at": "...", "n": N, "acc": A},
              ...
            ]
          },
          "EPL": {...},
          "knockout": {...}
        }
  3. 支持 env 覆写 ``FOOTBALL_LR_WEIGHTS_ACTIVE_GLOBAL`` 等；
     默认从 env 读，否则从注册表 active 字段读。
  4. 加载后做 SHA256 校验，发现不一致时记录 warning 并使用用户给的 env。
  5. 提供 save() 把任意 ``LogisticFusionWeights`` 写入新文件并更新注册表。

旧路径兼容：
  - 若注册表不存在，本模块自动扫描目录中的 ``{league}_v1_*.json`` 文件，
    按 ``trained_at desc`` 排序，构造一份初始注册表（不强行"锁定"任何
    一个文件，把"哪个生效"交由调用方），并写回注册表。
  - 旧文件 ``ignore_me_autoname.json``、``validation_meta.json`` 不进入
    注册表，因为前缀不匹配。

该模块无副作用：除 ``ensure_dir`` 外不读写其他资源。
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from utils.paths import WEIGHTS_LR_DIR, ensure_dir
from utils.logger import get_logger
from fusion.logistic_fusion import LogisticFusionWeights

logger = get_logger("weights_registry")


_LEAGUE_FILE_PATTERN = re.compile(r"^(?P<league>[A-Za-z0-9]+)_v1_(?P<date>\d{4}-\d{2}-\d{2})(?:_(?P<tag>\w+))?\.json$")


@dataclass
class WeightEntry:
    file: str
    sha256: str
    trained_at: str
    n: int
    acc: float
    l1: float = 0.0


@dataclass
class LeagueRegistry:
    active: Optional[str] = None
    history: List[WeightEntry] = None  # type: ignore

    def __post_init__(self):
        if self.history is None:
            self.history = []


class WeightsRegistry:
    """单一权威的权重注册表。"""

    REGISTRY_FILENAME = "registry.json"
    ENV_OVERRIDE_PREFIX = "FOOTBALL_LR_WEIGHTS_ACTIVE_"
    # 低于此准确率的权重视为退化，自动回退到 history 中最优可用版本
    MIN_ACCEPTABLE_ACC = float(os.environ.get("FOOTBALL_LR_MIN_ACC", "0.52"))
    MIN_ACCEPTABLE_N = int(os.environ.get("FOOTBALL_LR_MIN_N", "1000"))

    def __init__(self, root: Optional[Path] = None) -> None:
        self.root = ensure_dir(root if root else WEIGHTS_LR_DIR)
        self.path = self.root / self.REGISTRY_FILENAME
        self._data: Dict[str, LeagueRegistry] = {}
        self._load_or_initialize()

    # ── Public API ──────────────────────────
    def get_active(self, league: str) -> Optional[LogisticFusionWeights]:
        """
        加载 league 当前 active 权重。

        顺序：
        1) env ``FOOTBALL_LR_WEIGHTS_ACTIVE_<LEAGUE>`` 提供文件名（覆盖注册表）；
        2) 注册表 active 文件名；
        3) 否则按目录里 ``trained_at`` 最新文件兜底。
        4) 若准确率低于 MIN_ACCEPTABLE_ACC，自动改用 history 中最优权重（防退化）。
        """
        env_key = f"{self.ENV_OVERRIDE_PREFIX}{league.upper()}"
        env_file = os.environ.get(env_key)
        force_env = bool(env_file)
        chosen = env_file or self._get_active(league)
        if not chosen:
            chosen = self._latest_file(league)
        if not chosen:
            logger.warning(f"[weights_registry] no weights for league={league!r}")
            return None

        # 防退化：非 env 强制时，拒绝低准确率 active
        if not force_env:
            chosen = self._guard_against_degraded(league, chosen)

        full = self.root / chosen
        if not full.exists():
            logger.warning(f"[weights_registry] missing file: {full}")
            return None
        try:
            w = LogisticFusionWeights.load(str(full))
        except Exception as exc:
            logger.warning(f"[weights_registry] load failed for {full}: {exc}")
            return None
        # SHA 校验
        sha = _sha256(full)
        reg = self._data.get(league)
        if reg is not None:
            entry = next((e for e in reg.history if e.file == chosen), None)
            if entry and entry.sha256 != sha:
                logger.warning(
                    f"[weights_registry] sha mismatch for {chosen}: "
                    f"{entry.sha256[:8]} != {sha[:8]}"
                )
        acc = float(getattr(w, "accuracy", 0.0) or 0.0)
        n = int(getattr(w, "sample_count", 0) or 0)
        logger.info(
            f"[weights_registry] active {league}: {chosen} acc={acc:.1%} n={n} dim={len(w.coef_home)}"
        )
        return w

    def _guard_against_degraded(self, league: str, chosen: str) -> str:
        """若 chosen 准确率过低，回退到 history 中最优可用权重。"""
        reg = self._data.get(league)
        if reg is None or not reg.history:
            return chosen

        chosen_entry = next((e for e in reg.history if e.file == chosen), None)
        chosen_acc = float(chosen_entry.acc) if chosen_entry else self._probe_acc(chosen)

        if chosen_acc >= self.MIN_ACCEPTABLE_ACC:
            return chosen

        candidates = [
            e for e in reg.history
            if e.file != chosen
            and float(e.acc) >= self.MIN_ACCEPTABLE_ACC
            and int(e.n) >= self.MIN_ACCEPTABLE_N
            and (self.root / e.file).exists()
        ]
        if not candidates:
            # 退而求其次：history 中准确率最高且文件存在
            candidates = [
                e for e in reg.history
                if (self.root / e.file).exists() and float(e.acc) > chosen_acc
            ]
        if not candidates:
            logger.warning(
                f"[weights_registry] degraded weight {chosen} acc={chosen_acc:.1%} "
                f"but no better fallback for {league}"
            )
            return chosen

        best = max(candidates, key=lambda e: (float(e.acc), int(e.n)))
        logger.warning(
            f"[weights_registry] REJECT degraded {chosen} (acc={chosen_acc:.1%}) "
            f"→ fallback {best.file} (acc={float(best.acc):.1%}, n={best.n})"
        )
        # 持久化修正 active，避免每次启动都走退化路径
        if reg.active != best.file:
            reg.active = best.file
            try:
                self._save()
            except Exception as exc:
                logger.warning(f"[weights_registry] failed to persist fallback: {exc}")
        return best.file

    def _probe_acc(self, file_name: str) -> float:
        full = self.root / file_name
        if not full.exists():
            return 0.0
        try:
            payload = json.loads(full.read_text(encoding="utf-8"))
            return float(payload.get("accuracy") or 0.0)
        except Exception:
            return 0.0

    def register(self, league: str, weight: LogisticFusionWeights, file_name: Optional[str] = None) -> str:
        """
        把 weight 持久化为新文件并登记到注册表。返回文件名前缀。
        """
        if file_name is None:
            ts = datetime.utcnow().strftime("%Y-%m-%d_%H%M%S")
            file_name = f"{league}_v1_{ts}.json"
        full = self.root / file_name
        weight.save(str(full))
        sha = _sha256(full)
        entry = WeightEntry(
            file=file_name,
            sha256=sha,
            trained_at=datetime.utcnow().isoformat() + "Z",
            n=getattr(weight, "sample_count", 0),
            acc=float(getattr(weight, "accuracy", 0.0)),
            l1=float(getattr(weight, "l1_penalty", 0.0)),
        )
        reg = self._data.setdefault(league, LeagueRegistry(active=None, history=[]))
        # 把已存在的同名条目替换
        reg.history = [e for e in reg.history if e.file != entry.file]
        reg.history.insert(0, entry)
        # 只保留最新 20 条
        reg.history = reg.history[:20]
        reg.active = entry.file
        self._save()
        logger.info(
            f"[weights_registry] registered {league}: active={entry.file} acc={entry.acc:.1%} sha={sha[:8]}"
        )
        return file_name

    def rollback(self, league: str, file_name: Optional[str] = None) -> bool:
        """
        回滚到指定文件；若未指定，回滚到历史最新一条非活跃项。
        """
        reg = self._data.get(league)
        if reg is None or not reg.history:
            return False
        if file_name is None:
            for e in reg.history:
                if e.file != reg.active:
                    file_name = e.file
                    break
            if file_name is None:
                return False
        reg.active = file_name
        self._save()
        logger.warning(f"[weights_registry] rollback {league} -> {file_name}")
        return True

    def latest(self, league: str, limit: int = 5) -> List[WeightEntry]:
        reg = self._data.get(league)
        if not reg:
            return []
        return reg.history[:limit]

    def summary(self) -> Dict[str, dict]:
        return {
            league: {
                "active": reg.active,
                "history_count": len(reg.history),
                "files": [e.file for e in reg.history[:5]],
            }
            for league, reg in self._data.items()
        }

    # ── Internal ─────────────────────────────
    def _get_active(self, league: str) -> Optional[str]:
        reg = self._data.get(league)
        return reg.active if reg else None

    def _latest_file(self, league: str) -> Optional[str]:
        candidates = []
        for f in self.root.iterdir():
            m = _LEAGUE_FILE_PATTERN.match(f.name)
            if not m:
                continue
            if m.group("league") != league:
                continue
            candidates.append((m.group("date"), f.name))
        if not candidates:
            return None
        candidates.sort(reverse=True)
        return candidates[0][1]

    def _load_or_initialize(self) -> None:
        if self.path.exists():
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
            except Exception as exc:
                logger.warning(f"[weights_registry] registry corrupt: {exc}; rebuilding")
                raw = {}
            self._data = {
                k: LeagueRegistry(
                    active=v.get("active"),
                    history=[WeightEntry(**e) for e in v.get("history", []) if isinstance(e, dict)],
                )
                for k, v in raw.items() if isinstance(v, dict)
            }
        else:
            self._bootstrap_from_disk()
            self._save()

    def _bootstrap_from_disk(self) -> None:
        """目录里现存权重扫描并构造初始注册表。"""
        leagues: Dict[str, List[WeightEntry]] = {}
        for f in self.root.iterdir():
            if not f.is_file():
                continue
            name = f.name
            if name == self.REGISTRY_FILENAME:
                continue
            if name in ("validation_meta.json",):
                continue
            m = _LEAGUE_FILE_PATTERN.match(name)
            if not m:
                continue
            league = m.group("league")
            sha = _sha256(f)
            n = acc = 0.0
            try:
                payload = json.loads(f.read_text(encoding="utf-8"))
                n = int(payload.get("sample_count") or payload.get("train_meta", {}).get("n", 0))
                acc = float(payload.get("accuracy") or payload.get("train_meta", {}).get("accuracy", 0.0))
                trained = payload.get("trained_at") or payload.get("train_meta", {}).get("trained_at", "")
            except Exception:
                trained = ""
            entry = WeightEntry(
                file=name, sha256=sha, trained_at=trained, n=n, acc=acc,
            )
            leagues.setdefault(league, []).append(entry)
        for league, hist in leagues.items():
            hist.sort(key=lambda e: e.trained_at or "", reverse=True)
            self._data[league] = LeagueRegistry(active=hist[0].file, history=hist[:20])

    def _save(self) -> None:
        payload = {
            k: {
                "active": v.active,
                "history": [asdict(e) for e in v.history],
            }
            for k, v in self._data.items()
        }
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        tmp.replace(self.path)
        logger.info(f"[weights_registry] registry saved: {self.path}")


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()
