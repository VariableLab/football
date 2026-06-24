"""
比赛环境数据同步器 — 为比赛填充 weather, temperature, pitch_condition

策略:
1. 如果 venue 已知，尝试从 OpenWeatherMap 获取天气 (需要 API key)
2. 否则根据比赛所在城市的气候常识推断
3. 世界杯/友谊赛场地条件默认为 good

用法:
    cd backend && python ingestion/match_env_sync.py
    cd backend && python ingestion/match_env_sync.py --dry-run
"""
from __future__ import annotations

import os
import sys
import argparse
import requests
from datetime import datetime, timezone
from typing import Dict, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy.orm import Session
from database.models import SessionLocal, Match, MatchStatus, Team
from utils.logger import get_logger

logger = get_logger("match_env_sync")

# 城市 -> 默认气候条件映射 (当 API 不可用时使用)
CITY_CLIMATE: Dict[str, Dict] = {
    # 美国主要城市 (2026 WC 主办城市)
    "New York": {"weather": "clear", "temperature": 22.0, "pitch": "good"},
    "New Jersey": {"weather": "clear", "temperature": 21.0, "pitch": "good"},
    "Los Angeles": {"weather": "clear", "temperature": 24.0, "pitch": "good"},
    "Houston": {"weather": "hot", "temperature": 30.0, "pitch": "good"},
    "Dallas": {"weather": "hot", "temperature": 28.0, "pitch": "good"},
    "Atlanta": {"weather": "hot", "temperature": 26.0, "pitch": "good"},
    "Orlando": {"weather": "hot", "temperature": 29.0, "pitch": "good"},
    "Philadelphia": {"weather": "clear", "temperature": 20.0, "pitch": "good"},
    "Kansas City": {"weather": "clear", "temperature": 22.0, "pitch": "good"},
    "Seattle": {"weather": "clear", "temperature": 16.0, "pitch": "good"},
    "San Francisco": {"weather": "clear", "temperature": 17.0, "pitch": "good"},
    "Boston": {"weather": "clear", "temperature": 18.0, "pitch": "good"},
    "Monterrey": {"weather": "hot", "temperature": 30.0, "pitch": "good"},
    "Guadalajara": {"weather": "clear", "temperature": 24.0, "pitch": "good"},
    "Mexico City": {"weather": "clear", "temperature": 20.0, "pitch": "good"},
    # 常见球场
    "MetLife": {"weather": "clear", "temperature": 20.0, "pitch": "good"},
    "SoFi": {"weather": "clear", "temperature": 24.0, "pitch": "good"},
    "AT&T": {"weather": "hot", "temperature": 28.0, "pitch": "good"},
    "NRG": {"weather": "hot", "temperature": 30.0, "pitch": "good"},
    "Mercedes-Benz": {"weather": "hot", "temperature": 26.0, "pitch": "good"},
    "Oracle": {"weather": "clear", "temperature": 17.0, "pitch": "good"},
}

# 温度区间 -> weather 标签
def temp_to_weather(temp: float) -> str:
    if temp >= 30:
        return "hot"
    elif temp >= 25:
        return "clear"
    elif temp >= 10:
        return "clear"
    elif temp >= 0:
        return "cold"
    else:
        return "snow"


def get_weather_from_api(city: str) -> Optional[Dict]:
    """使用 OpenWeatherMap 免费 API 获取天气 (需要 OPENWEATHER_API_KEY)"""
    api_key = os.environ.get("OPENWEATHER_API_KEY", "")
    if not api_key:
        return None

    try:
        url = "https://api.openweathermap.org/data/2.5/weather"
        resp = requests.get(url, params={"q": city, "appid": api_key, "units": "metric"}, timeout=10)
        resp.raise_for_status()
        data = resp.json()

        return {
            "weather": "rain" if data.get("weather", [{}])[0].get("main") == "Rain" else "clear",
            "temperature": data.get("main", {}).get("temp", 20.0),
            "pitch": "good",
        }
    except Exception as e:
        logger.info(f"[match_env] Weather API error for {city}: {e}")
        return None


def get_weather_from_city(venue: str) -> Dict:
    """从城市气候映射获取天气信息"""
    for key, info in CITY_CLIMATE.items():
        if key.lower() in (venue or "").lower():
            return info.copy()
    return {"weather": "clear", "temperature": 20.0, "pitch": "good"}


def sync_match_environment(db: Session, dry_run: bool = False) -> Dict:
    """为所有未填充的比赛填充环境数据"""
    # 只处理 scheduled/upcoming 的比赛
    matches = db.query(Match).filter(
        Match.status.in_([MatchStatus.SCHEDULED, MatchStatus.UPCOMING]),
        Match.kickoff_at.isnot(None),
    ).all()

    stats = {"total": len(matches), "updated": 0, "already_set": 0, "errors": 0}

    for m in matches:
        try:
            venue = m.venue or ""
            updated = False

            # 检查是否已有环境数据
            if m.weather and m.weather != "clear" or (m.temperature and m.temperature != 20.0):
                stats["already_set"] += 1
                continue

            # 尝试 API
            weather_info = get_weather_from_api(venue)
            if not weather_info:
                weather_info = get_weather_from_city(venue)

            if not dry_run:
                if not m.weather:
                    m.weather = weather_info["weather"]
                    updated = True
                if not m.temperature or m.temperature == 20.0:
                    m.temperature = weather_info["temperature"]
                    updated = True
                if not m.pitch_condition:
                    m.pitch_condition = weather_info["pitch"]
                    updated = True

            if updated:
                stats["updated"] += 1
                logger.info(f"[match_env] {m.match_code}: venue={venue}, weather={weather_info['weather']}, temp={weather_info['temperature']}")

        except Exception as e:
            logger.error(f"[match_env] Error for {m.match_code}: {e}")
            stats["errors"] += 1

    if not dry_run:
        db.commit()

    return stats


def main():
    parser = argparse.ArgumentParser(description="比赛环境数据同步器")
    parser.add_argument("--dry-run", action="store_true", help="仅输出不写入")
    args = parser.parse_args()

    print("=" * 60)
    print("  比赛环境数据同步 (Match Environment Sync)")
    print("=" * 60)

    db = SessionLocal()
    try:
        t0 = __import__('time').time()
        stats = sync_match_environment(db, dry_run=args.dry_run)
        elapsed = __import__('time').time() - t0

        print(f"\n同步完成 ({elapsed:.1f}s):")
        print(f"  总比赛数: {stats['total']}")
        print(f"  已更新: {stats['updated']}")
        print(f"  已有数据: {stats['already_set']}")
        print(f"  错误: {stats['errors']}")

    finally:
        db.close()


if __name__ == "__main__":
    main()
