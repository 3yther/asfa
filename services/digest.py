"""Weekly Telegram digest (Tier 3 Part 5).

One Sunday-evening HTML message summarising the week across every ASFA module.
Design notes that matter:

* **HTML, not Markdown.** Telegram's MarkdownV2 rejects the whole message if any
  dynamic value contains an unescaped ``_``/``-``/``.`` (job titles, fragrance
  names). We use ``parse_mode="HTML"`` and ``html.escape`` every interpolated
  string, so user data can't break — or inject into — the message.
* **4096-char cap.** Telegram hard-limits a message to 4096 chars; we assemble
  sections most-important-first and drop the least-important (System/agents)
  first if we'd overflow.
* **Empty sections are omitted**, never shown blank.
* **Idempotent.** ``last_digest_sent_at`` in kv_store; the job skips if a digest
  already went out in the last 24h, protecting against a scheduler double-fire
  or a restart replaying the trigger.
"""

import html
import logging
from datetime import date, datetime, timedelta

import database as db
from services import telegram_bot

logger = logging.getLogger(__name__)

TELEGRAM_MAX = 4096
_KV_LAST_SENT = "last_digest_sent_at"


def _esc(v) -> str:
    return html.escape(str(v))


def _gym_section(start, end_excl):
    s = db.get_gym_week_summary(start, end_excl)
    if not s["sessions"]:
        return None
    bits = [f"{s['sessions']} session{'s' if s['sessions'] != 1 else ''}",
            f"{s['volume_kg']:g} kg volume"]
    if s["prs"]:
        bits.append(f"{s['prs']} PR{'s' if s['prs'] != 1 else ''} 🎉")
    if s["avg_rpe"] is not None:
        bits.append(f"avg RPE {s['avg_rpe']:g}")
    return "🏋️ <b>Gym</b>\n" + _esc(" · ".join(bits))


def _scout_section(start, end_excl):
    s = db.get_scout_week_summary(start, end_excl)
    if not (s["new_saved"] or s["new_applied"] or s["stage_changes"] or s["followups_due"]):
        return None
    lines = []
    if s["new_applied"]:
        lines.append(f"{s['new_applied']} new application{'s' if s['new_applied'] != 1 else ''}")
    if s["new_saved"]:
        lines.append(f"{s['new_saved']} saved")
    if s["stage_changes"]:
        lines.append(f"{s['stage_changes']} stage change{'s' if s['stage_changes'] != 1 else ''}")
    if s["followups_due"]:
        lines.append(f"{s['followups_due']} follow-up{'s' if s['followups_due'] != 1 else ''} due ⏰")
    return "🔍 <b>Scout</b>\n" + _esc(" · ".join(lines))


def _scent_section(start, end_excl):
    s = db.get_scent_week_summary(start, end_excl)
    if not s["wears"]:
        return None
    line = f"{s['wears']} wear{'s' if s['wears'] != 1 else ''}"
    body = "💨 <b>Scent</b>\n" + _esc(line)
    if s["top"]:
        body += "\nTop: <i>" + _esc(s["top"]) + "</i>"
    return body


def _body_section():
    scans = db.get_body_composition(days=14)  # newest-first
    if not scans:
        return None
    latest = scans[0]
    parts = []
    if latest.get("weight_kg") is not None:
        parts.append(f"{latest['weight_kg']:g} kg")
    if latest.get("body_fat_percent") is not None:
        parts.append(f"{latest['body_fat_percent']:g}% bf")
    if not parts:
        return None
    body = "⚖️ <b>Body</b>\n" + _esc(" · ".join(parts))
    # 7-day delta: earliest scan that is >= 7 days older than the latest.
    try:
        latest_d = date.fromisoformat(str(latest["date_scanned"])[:10])
        prior = next((s for s in scans
                      if (latest_d - date.fromisoformat(str(s["date_scanned"])[:10])).days >= 6), None)
        if prior and latest.get("weight_kg") is not None and prior.get("weight_kg") is not None:
            dw = latest["weight_kg"] - prior["weight_kg"]
            body += _esc(f"  ({'+' if dw >= 0 else ''}{round(dw, 1):g} kg / 7d)")
    except (ValueError, TypeError):
        pass
    return body


def _system_section(start_iso):
    """Least-important section — dropped first on overflow."""
    chain = db.verify_audit_chain()
    lockouts = db.count_security_lockouts(start_iso)
    energies = db.get_all_energy()
    low = [e for e in energies if (e.get("energy") or 0) < 40]
    lines = ["Audit chain: " + ("✅ valid" if chain["valid"] else "⚠️ BROKEN")]
    lines.append(f"Lockouts this week: {lockouts}")
    lines.append(f"Agents: {len(energies)} tracked" +
                 (f", {len(low)} low energy" if low else ", all healthy"))
    return "🤖 <b>System</b>\n" + _esc("\n".join(lines))


def build_weekly_digest() -> str:
    """Assemble the HTML digest, omitting empty modules. Returns "" if nothing to
    report. Sections are ordered most-important-first; System is truncated first
    to stay under Telegram's 4096-char cap."""
    today = date.today()
    start = (today - timedelta(days=6)).isoformat()
    end_excl = (today + timedelta(days=1)).isoformat()

    header = "📅 <b>ASFA Weekly Digest</b>\n<i>" + _esc(
        (today - timedelta(days=6)).strftime("%d %b") + " – " + today.strftime("%d %b %Y")) + "</i>"

    # (section_html, is_droppable) — most-important first; System droppable.
    sections = [
        (_gym_section(start, end_excl), False),
        (_scout_section(start, end_excl), False),
        (_scent_section(start, end_excl), False),
        (_body_section(), False),
        (_system_section(start + " 00:00:00"), True),
    ]
    present = [(html_, drop) for (html_, drop) in sections if html_]
    if not any(not drop for (html_, drop) in present):
        # Only System has content — still worth sending the weekly heartbeat, but
        # if literally nothing is present, send nothing.
        if not present:
            return ""

    def assemble(secs):
        return "\n\n".join([header] + [h for (h, _d) in secs])

    msg = assemble(present)
    # Drop droppable sections from the end until we fit under the cap.
    while len(msg) > TELEGRAM_MAX and any(d for (_h, d) in present):
        for i in range(len(present) - 1, -1, -1):
            if present[i][1]:
                present.pop(i)
                break
        msg = assemble(present)
    if len(msg) > TELEGRAM_MAX:
        msg = msg[:TELEGRAM_MAX - 1].rstrip() + "…"
    return msg


def send_weekly_digest(force: bool = False) -> dict:
    """Build and send the weekly digest. Idempotent: skips if one went out in the
    last 24h unless ``force`` (the manual send-now path). Returns a status dict."""
    if not force:
        last = db.kv_get(_KV_LAST_SENT)
        if last:
            try:
                if (datetime.utcnow() - datetime.fromisoformat(last)) < timedelta(hours=24):
                    return {"ok": True, "sent": False, "reason": "already sent in last 24h"}
            except (ValueError, TypeError):
                pass
    if not telegram_bot.is_configured():
        return {"ok": False, "sent": False, "reason": "telegram not configured"}
    msg = build_weekly_digest()
    if not msg:
        return {"ok": True, "sent": False, "reason": "no data this week"}
    telegram_bot.send_message(msg)
    db.kv_set(_KV_LAST_SENT, datetime.utcnow().isoformat())
    return {"ok": True, "sent": True, "chars": len(msg)}
