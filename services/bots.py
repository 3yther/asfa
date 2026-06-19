"""Trading-bot integration — pulls live state from the stock-scanner app.

Everything degrades gracefully: if the scanner is offline or an endpoint is
missing, we still return the dashboard links so the briefing card is useful.
"""
import logging
import time

import requests

logger = logging.getLogger("asfa.bots")

# Base of the deployed stock-scanner / crypto-bot app.
SCANNER_BASE = "https://stock-scanner-production-0b0d.up.railway.app"
SCANNER_URL = f"{SCANNER_BASE}/api/status"
TJR_STATUS_URL = f"{SCANNER_BASE}/api/tjr/status"
TJR_PORTFOLIO_URL = f"{SCANNER_BASE}/api/tjr/portfolio"

# Clickable dashboard links shown on the briefing card.
LINKS = {
    "crypto": f"{SCANNER_BASE}/crypto",
    "scanner": f"{SCANNER_BASE}/scanner",
}

TIMEOUT = 8


def _fetch(url, name):
    try:
        r = requests.get(url, timeout=TIMEOUT)
        r.raise_for_status()
        data = r.json()
        if isinstance(data, dict):
            data["bot_name"] = name
            data["online"] = True
            return data
        return {"bot_name": name, "online": True, "data": data}
    except requests.Timeout:
        return {"bot_name": name, "online": False, "error": "timeout"}
    except Exception as e:
        return {"bot_name": name, "online": False, "error": str(e)[:80]}


def get_bots_status():
    scanner = _fetch(SCANNER_URL, "Stock Scanner")
    return {"scanner": scanner}


def _latest_signal(tjr: dict):
    """Pick the most recent MSS across symbols from /api/tjr/status."""
    if not tjr or not tjr.get("online"):
        return None
    best = None
    for sym, s in (tjr.get("symbols") or {}).items():
        mss = s.get("latest_mss")
        if not mss:
            continue
        cand = {
            "symbol": sym,
            "direction": mss.get("direction"),
            "time": mss.get("time"),
            "price": mss.get("price"),
            "regime": s.get("regime"),
            "sweep_status": s.get("sweep_status"),
            "active_fvgs": s.get("active_fvg_count"),
        }
        if best is None or str(cand["time"] or "") > str(best["time"] or ""):
            best = cand
    return best


def get_trading_activity():
    """Live trading snapshot for the ASFA briefing card.

    Always returns the dashboard links. Adds live stats (regime, latest signal,
    portfolio P&L) when the scanner endpoints respond. Never raises.
    """
    result = {
        "links": dict(LINKS),
        "online": False,
        "regime": None,
        "regime_filter": None,
        "latest_signal": None,
        "portfolio": None,
    }

    tjr = _fetch(TJR_STATUS_URL, "Crypto Bot")
    if tjr.get("online"):
        result["online"] = True
        result["in_session"] = tjr.get("in_session")
        result["regime_filter"] = tjr.get("regime_filter")
        # Per-symbol regime summary, e.g. {"BTC": "TREND", "ETH": "RANGE"}
        result["regime"] = {
            sym: s.get("regime") for sym, s in (tjr.get("symbols") or {}).items()
        }
        result["latest_signal"] = _latest_signal(tjr)
    else:
        result["error"] = tjr.get("error")

    portfolio = _fetch(TJR_PORTFOLIO_URL, "Crypto Bot")
    if portfolio.get("online"):
        result["online"] = True
        result["portfolio"] = {
            "equity": portfolio.get("equity"),
            "balance": portfolio.get("balance"),
            "holdings_value": portfolio.get("holdings_value"),
            "total_pnl": portfolio.get("total_pnl"),
            "total_pnl_pct": portfolio.get("total_pnl_pct"),
            "holdings": portfolio.get("holdings"),
        }

    return result


# ── Bot health glance (cached ~60s) ─────────────────────────────────────────────

_HEALTH_CACHE = {"ts": 0.0, "data": None}


def _bot_health_entry(key, name, data, url, crypto=False):
    online = bool(data.get("online"))
    entry = {"key": key, "name": name, "online": online, "url": url,
             "status": "offline" if not online else "online", "last_signal": None}
    if not online:
        entry["error"] = data.get("error")
        return entry
    if crypto:
        entry["status"] = "in session" if data.get("in_session") else "online"
        sig = _latest_signal(data)
        if sig:
            entry["last_signal"] = (
                f"{sig['symbol']} MSS {sig.get('direction', '')} @ {sig.get('price', '')}".strip())
    else:
        # Stock-scanner /api/status — shape isn't guaranteed; degrade defensively.
        entry["status"] = str(data.get("status") or data.get("state") or "online")
        last = data.get("last_signal") or data.get("latest_signal") or data.get("last_trade")
        if isinstance(last, dict):
            sym = last.get("symbol")
            entry["last_signal"] = (f"{sym} {last.get('direction', '')}".strip()
                                    if sym else str(last)[:60])
        elif last:
            entry["last_signal"] = str(last)[:60]
    return entry


def get_bots_health():
    """Per-bot alive/status/last-signal for the TRADING SYSTEMS card. Cached
    ~60s so the dashboard polling doesn't hammer the scanner. Never raises."""
    now = time.time()
    if _HEALTH_CACHE["data"] is not None and now - _HEALTH_CACHE["ts"] < 60:
        return _HEALTH_CACHE["data"]
    scanner = _fetch(SCANNER_URL, "Stock Scanner")
    tjr = _fetch(TJR_STATUS_URL, "Crypto Bot")
    data = {
        "updated": now,
        "bots": [
            _bot_health_entry("scanner", "Stock Scanner", scanner, LINKS["scanner"]),
            _bot_health_entry("crypto", "Crypto Bot", tjr, LINKS["crypto"], crypto=True),
        ],
    }
    _HEALTH_CACHE.update(ts=now, data=data)
    return data


def get_bots_summary_text(status=None):
    """One-line text summary used inside the AI context / briefing prompt."""
    activity = get_trading_activity()
    if not activity["online"]:
        return f"Trading bots: offline ({activity.get('error', 'no response')})"

    parts = []
    p = activity.get("portfolio")
    if p:
        parts.append(
            f"Crypto Bot equity=${p.get('equity', '?')}, "
            f"P&L=${p.get('total_pnl', '?')} ({p.get('total_pnl_pct', '?')}%)"
        )
    sig = activity.get("latest_signal")
    if sig:
        parts.append(
            f"latest signal: {sig.get('symbol')} MSS {sig.get('direction')} "
            f"@ {sig.get('price')} [{sig.get('regime')}]"
        )
    if activity.get("regime"):
        regimes = ", ".join(f"{k}:{v}" for k, v in activity["regime"].items())
        parts.append(f"regime: {regimes}")
    return "; ".join(parts) or "Crypto Bot online"
