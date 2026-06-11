import os
import requests

WEATHER_API_KEY = os.environ.get("WEATHER_API_KEY", "")
CITY = "London"
BASE = "https://api.openweathermap.org/data/2.5"


def get_weather():
    if not WEATHER_API_KEY:
        return {"error": "No WEATHER_API_KEY set", "temp": "?", "description": "unavailable", "icon": "01d"}
    try:
        r = requests.get(
            f"{BASE}/weather",
            params={"q": CITY, "appid": WEATHER_API_KEY, "units": "metric"},
            timeout=5)
        r.raise_for_status()
        d = r.json()
        return {
            "temp": round(d["main"]["temp"]),
            "feels_like": round(d["main"]["feels_like"]),
            "description": d["weather"][0]["description"].capitalize(),
            "icon": d["weather"][0]["icon"],
            "humidity": d["main"]["humidity"],
            "wind_speed": round(d["wind"]["speed"]),
            "city": CITY,
        }
    except Exception as e:
        return {"error": str(e), "temp": "?", "description": "unavailable", "icon": "01d"}


def get_forecast():
    if not WEATHER_API_KEY:
        return []
    try:
        r = requests.get(
            f"{BASE}/forecast",
            params={"q": CITY, "appid": WEATHER_API_KEY, "units": "metric", "cnt": 8},
            timeout=5)
        r.raise_for_status()
        items = r.json().get("list", [])
        return [
            {
                "time": it["dt_txt"],
                "temp": round(it["main"]["temp"]),
                "description": it["weather"][0]["description"].capitalize(),
                "icon": it["weather"][0]["icon"],
            }
            for it in items[:4]
        ]
    except Exception:
        return []
