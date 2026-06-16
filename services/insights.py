"""Autonomous intelligence — turns ASFA from reactive to proactive.

`gather_metrics()`  → 7-day trends for water, sleep, weight, spending, trading.
`generate_insights()` → 1-2 natural-language insights (Claude, with a safe
                        rule-based fallback when the API key is missing).
`predictive_alerts()` → rule-based concerning patterns worth a proactive push.

Everything degrades gracefully and never raises into the scheduler/briefing.
"""
import json
import logging
from datetime import datetime, timedelta

import database as db
from services import ai
from services.bots import get_trading_activity

logger = logging.getLogger("asfa.insights")


def _avg(vals):
    vals = [v for v in vals if v is not None]
    return sum(vals) / len(vals) if vals else 0.0


def _pct_change(recent, prev):
    if not prev:
        return None
    return (recent - prev) / prev * 100.0


def gather_metrics() -> dict:
    """Compute last-7-days-vs-prior trends from the local DB plus live trading."""
    today = datetime.now().date()
    cut = (today - timedelta(days=7)).isoformat()

    m = {}

    # ── Hydration & sleep (habits table, last 14 days) ──────────────────────────
    try:
        habits = db.get_habits(14)
        recent = [h for h in habits if h["date"] > cut]
        prev = [h for h in habits if h["date"] <= cut]
        w_recent = _avg([h.get("water_ml") for h in recent])
        w_prev = _avg([h.get("water_ml") for h in prev])
        m["water"] = {
            "recent_avg_ml": round(w_recent),
            "prev_avg_ml": round(w_prev),
            "change_pct": _pct_change(w_recent, w_prev),
            "streak": db.get_water_streak(),
        }
        s_recent = _avg([h.get("sleep_hours") for h in recent if h.get("sleep_hours")])
        m["sleep"] = {"recent_avg_h": round(s_recent, 1)}
    except Exception as e:
        logger.warning("metrics: habits failed: %s", e)

    # ── Body weight (first vs last over ~14 days) ───────────────────────────────
    try:
        bw = db.get_body_weight(14)  # ascending by date
        if len(bw) >= 2:
            delta = bw[-1]["weight_kg"] - bw[0]["weight_kg"]
            m["weight"] = {
                "latest_kg": bw[-1]["weight_kg"],
                "delta_kg": round(delta, 1),
                "direction": "up" if delta > 0.3 else "down" if delta < -0.3 else "stable",
            }
    except Exception as e:
        logger.warning("metrics: weight failed: %s", e)

    # ── Spending (recent 7 vs prior 7) ──────────────────────────────────────────
    try:
        sp = db.get_spending(14)
        recent_total = sum(s["amount"] for s in sp if s["date"] > cut)
        prev_total = sum(s["amount"] for s in sp if s["date"] <= cut)
        by_cat = {}
        for s in sp:
            if s["date"] > cut:
                by_cat[s["category"]] = round(by_cat.get(s["category"], 0) + s["amount"], 2)
        top = max(by_cat.items(), key=lambda kv: kv[1]) if by_cat else None
        m["spending"] = {
            "recent_total": round(recent_total, 2),
            "prev_total": round(prev_total, 2),
            "change_pct": _pct_change(recent_total, prev_total),
            "top_category": top[0] if top else None,
            "top_amount": top[1] if top else None,
        }
    except Exception as e:
        logger.warning("metrics: spending failed: %s", e)

    # ── Trading performance (live, from scanner API) ────────────────────────────
    try:
        trading = get_trading_activity()
        if trading.get("online") and trading.get("portfolio"):
            p = trading["portfolio"]
            m["trading"] = {
                "equity": p.get("equity"),
                "total_pnl": p.get("total_pnl"),
                "total_pnl_pct": p.get("total_pnl_pct"),
                "regime": trading.get("regime"),
            }
    except Exception as e:
        logger.warning("metrics: trading failed: %s", e)

    return m


def _metrics_text(m: dict) -> str:
    parts = []
    w = m.get("water")
    if w:
        chg = f"{w['change_pct']:+.0f}%" if w.get("change_pct") is not None else "n/a"
        parts.append(f"Water: {w['recent_avg_ml']}ml/day avg (vs {w['prev_avg_ml']} prior, {chg}), streak {w['streak']}d")
    s = m.get("sleep")
    if s:
        parts.append(f"Sleep: {s['recent_avg_h']}h/night avg")
    wt = m.get("weight")
    if wt:
        parts.append(f"Weight: {wt['latest_kg']}kg, {wt['delta_kg']:+}kg over 2 weeks ({wt['direction']})")
    sp = m.get("spending")
    if sp:
        chg = f"{sp['change_pct']:+.0f}%" if sp.get("change_pct") is not None else "n/a"
        parts.append(f"Spending: £{sp['recent_total']} this week (vs £{sp['prev_total']} prior, {chg}); top: {sp.get('top_category')} £{sp.get('top_amount')}")
    tr = m.get("trading")
    if tr:
        parts.append(f"Trading: equity ${tr['equity']}, P&L ${tr['total_pnl']} ({tr['total_pnl_pct']}%), regime {tr.get('regime')}")
    return "\n".join(f"- {p}" for p in parts) or "- No data yet"


def _fallback_insights(m: dict) -> list:
    """Deterministic insights used when Claude is unavailable."""
    out = []
    w = m.get("water")
    if w and w.get("change_pct") is not None:
        if w["change_pct"] <= -20:
            out.append(f"Your water intake is down {abs(w['change_pct']):.0f}% this week — worth rehydrating.")
        elif w["change_pct"] >= 20:
            out.append(f"Hydration is up {w['change_pct']:.0f}% this week — nice consistency.")
    tr = m.get("trading")
    if tr and tr.get("total_pnl_pct") is not None and tr["total_pnl_pct"] >= 5:
        out.append(f"Your trading bot is up {tr['total_pnl_pct']}% this week — consider taking some profit.")
    sp = m.get("spending")
    if sp and sp.get("change_pct") is not None and sp["change_pct"] >= 25:
        out.append(f"Spending is up {sp['change_pct']:.0f}% week-on-week, led by {sp.get('top_category')}.")
    return out[:2]


def generate_insights(metrics: dict = None) -> list:
    """Return 1-2 short, specific, cross-referenced insights about Amir's week."""
    m = metrics if metrics is not None else gather_metrics()
    c = ai._get_client()
    if not c:
        return _fallback_insights(m)
    try:
        resp = c.messages.create(
            model=ai.MODEL,
            max_tokens=220,
            messages=[{
                "role": "user",
                "content": (
                    "You are ASFA, Amir's proactive assistant. Based on these "
                    "7-day metrics, write 1-2 SHORT, specific insights (one "
                    "sentence each). Cross-reference where it's interesting "
                    "(e.g. spending vs weight, water vs trading days). Be concrete "
                    "with the numbers. Return ONLY a JSON array of strings.\n\n"
                    + _metrics_text(m)
                ),
            }],
        )
        text = resp.content[0].text.strip()
        start, end = text.find("["), text.rfind("]")
        if start != -1 and end != -1:
            arr = json.loads(text[start:end + 1])
            return [str(x) for x in arr][:2]
    except Exception as e:
        logger.warning("generate_insights failed: %s", e)
    return _fallback_insights(m)


def predictive_alerts(metrics: dict = None) -> list:
    """Rule-based concerning patterns. Returns [{message, kind}] to push."""
    m = metrics if metrics is not None else gather_metrics()
    alerts = []

    tr = m.get("trading")
    if tr and tr.get("total_pnl_pct") is not None:
        if tr["total_pnl_pct"] >= 5:
            alerts.append({
                "message": f"📈 Your trading bot is up {tr['total_pnl_pct']}% this week. Consider taking profits.",
                "kind": "alert",
            })
        elif tr["total_pnl_pct"] <= -5:
            alerts.append({
                "message": f"📉 Your trading bot is down {tr['total_pnl_pct']}% this week. Review risk/sizing.",
                "kind": "alert",
            })

    w = m.get("water")
    if w and w.get("change_pct") is not None and w["change_pct"] <= -30:
        alerts.append({
            "message": f"💧 Water intake down {abs(w['change_pct']):.0f}% this week. You might be dehydrated.",
            "kind": "alert",
        })

    s = m.get("sleep")
    if s and 0 < s.get("recent_avg_h", 0) < 6:
        alerts.append({
            "message": f"🌙 Sleep averaging {s['recent_avg_h']}h — below 6h. Protect your recovery tonight.",
            "kind": "alert",
        })

    wt = m.get("weight")
    sp = m.get("spending")
    if (wt and wt.get("direction") == "up" and wt.get("delta_kg", 0) >= 0.9
            and sp and (sp.get("change_pct") or 0) >= 20):
        alerts.append({
            "message": (f"⚖️ Weight up {wt['delta_kg']}kg while spending rose "
                        f"{sp['change_pct']:.0f}% — possible stress-spending pattern."),
            "kind": "alert",
        })

    return alerts
