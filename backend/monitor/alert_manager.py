"""
监控告警管理 — 检测连续失败、数据过期、引擎异常
告警方式：日志 + 文件标记 + Webhook（可选）
"""
import json
import os
from datetime import datetime, timezone
from typing import Optional

from utils.logger import get_logger

logger = get_logger("alert")

_BASE = os.path.dirname(os.path.abspath(__file__))
ALERT_FILE = os.path.join(_BASE, "data", "alerts.json")
MAX_ALERT_AGE_HOURS = 24
# 从环境变量读取外部通知 Webhook（支持 ServerChan/Bark/企业微信等）
ALERT_WEBHOOK_URL = os.environ.get("ALERT_WEBHOOK_URL", "")


def _load_alerts() -> list:
    if not os.path.exists(ALERT_FILE):
        return []
    try:
        with open(ALERT_FILE, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return []


def _save_alerts(alerts: list) -> None:
    os.makedirs(os.path.dirname(ALERT_FILE), exist_ok=True)
    #清理过期告警
    cutoff = datetime.now(timezone.utc).timestamp() - MAX_ALERT_AGE_HOURS * 3600
    fresh = [a for a in alerts if a.get("ts", 0) > cutoff]
    with open(ALERT_FILE, "w") as f:
        json.dump(fresh[-50:], f, indent=2)


def _notify_telegram(source: str, level: str, message: str) -> None:
    """发送 Telegram 消息告警"""
    from database.config import get_settings
    try:
        s = get_settings()
    except Exception:
        return
        
    if not s.TELEGRAM_BOT_TOKEN or not s.TELEGRAM_CHAT_ID:
        return
    
    try:
        import httpx
        url = f"https://api.telegram.org/bot{s.TELEGRAM_BOT_TOKEN}/sendMessage"
        emoji = "🔴" if level == "critical" else "⚠️"
        text = (
            f"{emoji} *WC Analytics Alert*\n"
            f"━━━━━━━━━━━━━━━\n"
            f"*Source:* `{source}`\n"
            f"*Level:* `{level.upper()}`\n"
            f"*Time:* `{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC`\n\n"
            f"{message}"
        )
        payload = {
            "chat_id": s.TELEGRAM_CHAT_ID,
            "text": text,
            "parse_mode": "Markdown",
        }
        httpx.post(url, json=payload, timeout=10)
    except Exception as e:
        logger.warning(f"[alert] telegram notification failed: {e}")


def _notify_webhook(source: str, level: str, message: str) -> None:
    if not ALERT_WEBHOOK_URL:
        return
    try:
        import httpx
        payload = {
            "source": source,
            "level": level,
            "message": message,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        httpx.post(ALERT_WEBHOOK_URL, json=payload, timeout=5)
    except Exception:
        logger.warning(f"[alert] webhook notification failed (ignored)")


def fire_alert(source: str, level: str, message: str) -> None:
    """发出告警并持久化"""
    alert = {
        "source": source,
        "level": level,
        "message": message,
        "ts": datetime.now(timezone.utc).timestamp(),
        "time": datetime.now(timezone.utc).isoformat(),
    }
    alerts = _load_alerts()
    #去重：同 source 30分钟内不重复，防止消息微小差异导致的轰炸
    recent = [a for a in alerts
              if a["source"] == source
              and a["ts"] > alert["ts"] - 1800]
    if not recent:
        alerts.append(alert)
        _save_alerts(alerts)
        log_fn = logger.critical if level == "critical" else logger.warning
        log_fn(f"[ALERT] {source} | {level} | {message}")
        _notify_webhook(source, level, message)
        _notify_telegram(source, level, message)


def check_consecutive_failures(source: str, max_failures: int = 3) -> None:
    """检查某数据源连续失败次数，超过阈值触发告警"""
    alerts = _load_alerts()
    recent = [a for a in alerts if a["source"] == source and a["ts"] > datetime.now(timezone.utc).timestamp() - 3600]
    failures = [a for a in recent if "failed" in a.get("message", "").lower() or "error" in a.get("message", "").lower()]
    if len(failures) >= max_failures:
        fire_alert(
            source=source,
            level="critical",
            message=f"连续 {len(failures)} 次失败，请检查数据源连接",
        )


def get_active_alerts() -> list:
    """获取当前活跃告警列表"""
    return _load_alerts()


def odds_freshness_check(db) -> dict:
    """检查赔率数据新鲜度"""
    from database.models import Match, MatchBookmakerOdds, MatchStatus
    now = datetime.now(timezone.utc)

    upcoming = db.query(Match).filter(
        Match.status.in_([MatchStatus.SCHEDULED, MatchStatus.UPCOMING]),
        Match.kickoff_at > now,
    ).all()

    stale_count = 0
    for match in upcoming:
        latest_odds = db.query(MatchBookmakerOdds).filter(
            MatchBookmakerOdds.match_id == match.id,
        ).order_by(MatchBookmakerOdds.id.desc()).first()

        if not latest_odds:
            stale_count += 1
            continue

        updated = getattr(latest_odds, "updated_at", None) or getattr(latest_odds, "created_at", None)
        if updated and (now - updated).total_seconds() > 7200:  #2小时
            stale_count += 1

    if stale_count > 0:
        hours_to_kickoff = 48
        critical_matches = db.query(Match).filter(
            Match.status.in_([MatchStatus.SCHEDULED, MatchStatus.UPCOMING]),
            Match.kickoff_at <= now + __import__("datetime").timedelta(hours=6),
        ).count()

        if critical_matches > 0 and stale_count > 0:
            fire_alert(
                source="odds_freshness",
                level="critical",
                message=f"6小时内 {critical_matches} 场比赛有 {stale_count} 场赔率过期",
            )

    return {"upcoming": len(upcoming), "stale": stale_count}
