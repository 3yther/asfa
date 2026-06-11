import os
import requests

NEWS_API_KEY = os.environ.get("NEWS_API_KEY", "")
BASE = "https://newsapi.org/v2"


def _fetch(endpoint, params):
    if not NEWS_API_KEY:
        return []
    try:
        params["apiKey"] = NEWS_API_KEY
        r = requests.get(f"{BASE}/{endpoint}", params=params, timeout=6)
        r.raise_for_status()
        articles = r.json().get("articles", [])
        return [
            {
                "title": a["title"],
                "source": a["source"]["name"],
                "url": a["url"],
                "description": a.get("description", ""),
                "published": a.get("publishedAt", ""),
            }
            for a in articles
            if a.get("title") and "[Removed]" not in a.get("title", "")
        ]
    except Exception:
        return []


def get_top_news():
    return _fetch("top-headlines", {"sources": "bbc-news", "pageSize": 6})


def get_finance_news():
    return _fetch("everything", {
        "q": "stock market OR finance OR trading",
        "language": "en",
        "sortBy": "publishedAt",
        "pageSize": 5,
    })
