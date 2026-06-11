import requests

QUANT_BOT_URL = "https://quant-bot-production-96db.up.railway.app/api/status"
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
    quant = _fetch(QUANT_BOT_URL, "Quant Bot")
    scanner = _fetch(SCANNER_URL, "Stock Scanner")
    return {"quant": quant, "scanner": scanner}


def get_bots_summary_text(status=None):
    if status is None:
        status = get_bots_status()
    lines = []
    for key in ("quant", "scanner"):
        b = status[key]
        if not b.get("online"):
            lines.append(f"{b['bot_name']}: offline ({b.get('error', '')})")
        else:
            equity = b.get("equity") or b.get("portfolio_value") or "?"
            pnl = b.get("pnl") or b.get("daily_pnl") or b.get("total_pnl") or "?"
            positions = b.get("positions") or b.get("open_positions") or 0
            lines.append(f"{b['bot_name']}: equity={equity}, P&L={pnl}, positions={positions}")
    return "\n".join(lines)
