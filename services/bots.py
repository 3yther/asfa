import requests

SCANNER_URL = "https://stock-scanner-production-0b0d.up.railway.app/api/status"
TIMEOUT = 8


def _fetch(url, name):
    try:
        r = requests.get(url, timeout=TIMEOUT)
        r.raise_for_status()
        data = r.json()
        data["bot_name"] = name
        data["online"] = True
        return data
    except requests.Timeout:
        return {"bot_name": name, "online": False, "error": "timeout"}
    except Exception as e:
        return {"bot_name": name, "online": False, "error": str(e)[:80]}


def get_bots_status():
    scanner = _fetch(SCANNER_URL, "Stock Scanner")
    return {"scanner": scanner}


def get_bots_summary_text(status=None):
    if status is None:
        status = get_bots_status()
    b = status["scanner"]
    if not b.get("online"):
        return f"Stock Scanner: offline ({b.get('error', '')})"
    equity = b.get("equity") or b.get("portfolio_value") or "?"
    pnl = b.get("pnl") or b.get("daily_pnl") or b.get("total_pnl") or "?"
    positions = b.get("positions") or b.get("open_positions") or 0
    return f"Stock Scanner: equity={equity}, P&L={pnl}, positions={positions}"
