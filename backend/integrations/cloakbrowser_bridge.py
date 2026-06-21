"""
CloakBrowser 桥接模块 — 使用 cloakbrowser (Playwright stealth) 渲染 JS 页面

cloakbrowser 是 Playwright 的隐身替代品，48 项 C++ 补丁通过所有机器人检测。
此模块通过 Node.js 子进程调用 cloakbrowser，将渲染后的 HTML 返回给 Python。

适用场景:
1. BetExplorer — JS 渲染赔率页面
2. OddsPortal (via OddsHarvester) — 需要绕过 Cloudflare
3. 澳门彩票 / 香港马会 — 反爬较严的站点
4. 任何需要 JS 渲染的网页

安装:
  npm install cloakbrowser playwright-core
  npx cloakbrowser install
"""

import json
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from utils.logger import get_logger

logger = get_logger("cloakbrowser")

# ─── 常量 ──────────────────────────────────────────
NODE_SCRIPT_DIR = Path(__file__).parent / "_cloak_scripts"
NODE_SCRIPT_DIR.mkdir(exist_ok=True)

CLOAKBROWSER_NPM_DIR = Path("/Users/liuxuran/Github/node_modules/cloakbrowser")
NODE_BIN = shutil.which("node") or "node"
NPM_ROOT = str(CLOAKBROWSER_NPM_DIR.parent)


# ─── 数据结构 ──────────────────────────────────────
@dataclass(frozen=True)
class RenderedPage:
    """cloakbrowser 渲染结果"""
    url: str
    html: str
    title: str
    status_code: int
    rendered_at: datetime
    elapsed_ms: float


@dataclass(frozen=True)
class RenderedMatchOdds:
    """从渲染页面提取的赔率"""
    source: str
    url: str
    odds_home: Optional[float]
    odds_draw: Optional[float]
    odds_away: Optional[float]
    extra: Optional[Dict[str, Any]] = None


# ─── Node.js 渲染脚本 ──────────────────────────────
# Script uses direct path to cloakbrowser dist to avoid NODE_PATH/ESM issues
# process.argv[2] = JSON urls, process.argv[3] = timeout ms
_FETCH_SCRIPT_PATH = NODE_SCRIPT_DIR / "fetch_pages.mjs"


# ─── 核心: Python → Node.js 桥接 ──────────────────
class CloakBrowserBridge:
    """
    Python 桥接 cloakbrowser (Node.js Playwright stealth)。

    用法:
        bridge = CloakBrowserBridge()
        pages = bridge.render_pages([
            {"url": "https://www.betexplorer.com/soccer/england/premier-league/"},
            {"url": "https://example.com", "waitSelector": ".odds-table"},
        ])
    """

    def __init__(self, timeout: int = 30):
        self.timeout = timeout
        self._available: Optional[bool] = None

    def is_available(self) -> bool:
        """检查 cloakbrowser 是否可用"""
        if self._available is not None:
            return self._available

        if not CLOAKBROWSER_NPM_DIR.exists():
            logger.warning("[cloakbrowser] npm package not found at %s", CLOAKBROWSER_NPM_DIR)
            self._available = False
            return False

        if not shutil.which(NODE_BIN):
            logger.warning("[cloakbrowser] Node.js not found")
            self._available = False
            return False

        self._available = True
        return True

    def render_pages(
        self,
        page_configs: List[Dict[str, Any]],
    ) -> List[RenderedPage]:
        """
        渲染多个 URL 并返回 HTML。

        Args:
            page_configs: [{"url": "...", "waitSelector": "...", "waitMs": 1000}]

        Returns:
            List[RenderedPage]
        """
        if not self.is_available():
            return []

        serialized = json.dumps(page_configs)

        try:
            result = subprocess.run(
                [
                    NODE_BIN,
                    str(_FETCH_SCRIPT_PATH),
                    serialized,
                    str(self.timeout * 1000),
                ],
                capture_output=True,
                text=True,
                timeout=self.timeout * len(page_configs) + 60,
            )

            if result.returncode != 0:
                logger.error("[cloakbrowser] Node.js error: %s", result.stderr[:500])
                return []

            data = json.loads(result.stdout)
            pages = []
            for item in data:
                pages.append(RenderedPage(
                    url=item.get("url", ""),
                    html=item.get("html", ""),
                    title=item.get("title", ""),
                    status_code=item.get("statusCode", 0),
                    rendered_at=datetime.now(timezone.utc),
                    elapsed_ms=item.get("elapsedMs", 0),
                ))
            logger.info(
                "[cloakbrowser] Rendered %d/%d pages",
                sum(1 for p in pages if p.status_code == 200),
                len(page_configs),
            )
            return pages

        except subprocess.TimeoutExpired:
            logger.error("[cloakbrowser] Subprocess timed out")
            return []
        except json.JSONDecodeError as e:
            logger.error("[cloakbrowser] JSON parse error: %s", e)
            return []
        except Exception as e:
            logger.error("[cloakbrowser] Unexpected error: %s", e)
            return []

    def render_single(self, url: str, wait_selector: str = "", wait_ms: int = 0) -> Optional[RenderedPage]:
        """渲染单个 URL"""
        config = {"url": url}
        if wait_selector:
            config["waitSelector"] = wait_selector
        if wait_ms:
            config["waitMs"] = wait_ms

        pages = self.render_pages([config])
        return pages[0] if pages else None


# ─── 模块级单例 ─────────────────────────────────────
_bridge: Optional[CloakBrowserBridge] = None


def get_bridge() -> CloakBrowserBridge:
    """获取全局 CloakBrowserBridge 实例"""
    global _bridge
    if _bridge is None:
        _bridge = CloakBrowserBridge()
    return _bridge


def is_cloakbrowser_available() -> bool:
    """检查 cloakbrowser 是否可用"""
    return get_bridge().is_available()
