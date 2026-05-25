"""weather 工具(M7 真实版)。

数据源:open-meteo —— 完全免费免 key。
  - 地理编码:https://geocoding-api.open-meteo.com/v1/search?name=<city>
  - 天气:https://api.open-meteo.com/v1/forecast?latitude=&longitude=&current=...

降级策略:
  - `cfg.tools.weather_mock=true` → 直接返回 mock(离线/测试用)
  - 常见城市先用内置坐标,减少 geocoding 依赖
  - 网络异常 / 解析失败 → 返回 mock 并在 note 字段标注 "fallback: <原因>",
    不让天气 API 把对话撞死
"""

from __future__ import annotations

import logging

import httpx

from .context import get_config
from .registry import tool

logger = logging.getLogger(__name__)


GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"


_KNOWN_LOCATIONS: dict[str, tuple[str, float, float]] = {
    "北京": ("北京", 39.9042, 116.4074),
    "上海": ("上海", 31.2304, 121.4737),
    "广州": ("广州", 23.1291, 113.2644),
    "深圳": ("深圳", 22.5431, 114.0579),
    "杭州": ("杭州", 30.2741, 120.1551),
    "南京": ("南京", 32.0603, 118.7969),
    "成都": ("成都", 30.5728, 104.0668),
    "重庆": ("重庆", 29.5630, 106.5516),
    "武汉": ("武汉", 30.5928, 114.3055),
    "西安": ("西安", 34.3416, 108.9398),
    "天津": ("天津", 39.3434, 117.3616),
    "苏州": ("苏州", 31.2989, 120.5853),
    "new york": ("New York", 40.7128, -74.0060),
    "london": ("London", 51.5072, -0.1276),
    "tokyo": ("Tokyo", 35.6762, 139.6503),
    "singapore": ("Singapore", 1.3521, 103.8198),
}


# open-meteo 的 weather_code → 中文简述
# 完整列表见 https://open-meteo.com/en/docs#weathervariables
_WEATHER_CODE_ZH: dict[int, str] = {
    0: "晴",
    1: "大体晴朗",
    2: "局部多云",
    3: "阴",
    45: "雾",
    48: "冻雾",
    51: "毛毛雨",
    53: "中毛毛雨",
    55: "大毛毛雨",
    61: "小雨",
    63: "中雨",
    65: "大雨",
    71: "小雪",
    73: "中雪",
    75: "大雪",
    80: "阵雨",
    81: "强阵雨",
    82: "暴雨",
    95: "雷阵雨",
    96: "雷阵雨伴冰雹",
    99: "强雷暴",
}


def _mock_response(city: str, note: str) -> dict:
    return {
        "city": city,
        "condition": "晴",
        "temperature_c": 22,
        "humidity": 45,
        "wind_kph": 8.0,
        "note": note,
    }


def _normalize_city(city: str) -> str:
    cleaned = (city or "").strip()
    for suffix in ("天气", "市", "当前天气", "今日天气"):
        if cleaned.endswith(suffix):
            cleaned = cleaned[: -len(suffix)].strip()
    return cleaned or city


def _known_location(city: str) -> tuple[str, float, float] | None:
    normalized = _normalize_city(city)
    lower = normalized.lower()
    if normalized in _KNOWN_LOCATIONS:
        return _KNOWN_LOCATIONS[normalized]
    if lower in _KNOWN_LOCATIONS:
        return _KNOWN_LOCATIONS[lower]
    for key, loc in _KNOWN_LOCATIONS.items():
        if key in normalized or key in lower:
            return loc
    return None


@tool(name="weather", description="查询某个城市当前的天气情况。")
async def weather(city: str) -> dict:
    """查询某个城市当前的天气情况。

    参数:
      city: 城市名称,例如 "北京"、"Shanghai"、"Tokyo"
    """
    cfg = get_config()
    if cfg.tools.weather_mock:
        return _mock_response(city, "mock 模式(config.tools.weather_mock=true)")

    city = _normalize_city(city)
    timeout = cfg.tools.http_timeout
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            known = _known_location(city)
            if known is not None:
                resolved_name, lat, lon = known
            else:
                geo = await client.get(
                    GEOCODE_URL,
                    params={"name": city, "count": 1, "language": "zh"},
                )
                geo.raise_for_status()
                geo_data = geo.json()
                results = geo_data.get("results") or []
                if not results:
                    return _mock_response(city, f"fallback: geocoding 未找到 {city}")
                loc = results[0]
                lat = loc["latitude"]
                lon = loc["longitude"]
                resolved_name = loc.get("name", city)

            fc = await client.get(
                FORECAST_URL,
                params={
                    "latitude": lat,
                    "longitude": lon,
                    "current": "temperature_2m,relative_humidity_2m,wind_speed_10m,weather_code",
                    "wind_speed_unit": "kmh",
                },
            )
            fc.raise_for_status()
            cur = (fc.json().get("current") or {})
    except (httpx.HTTPError, ValueError, KeyError) as e:
        logger.info("weather API unavailable, using fallback: %s", type(e).__name__)
        return _mock_response(city, f"fallback: {type(e).__name__}")

    wcode = int(cur.get("weather_code", 0) or 0)
    return {
        "city": resolved_name,
        "condition": _WEATHER_CODE_ZH.get(wcode, f"未知({wcode})"),
        "temperature_c": cur.get("temperature_2m"),
        "humidity": cur.get("relative_humidity_2m"),
        "wind_kph": cur.get("wind_speed_10m"),
        "note": "来源 open-meteo",
    }
