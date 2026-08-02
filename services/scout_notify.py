"""Scout's single alert path — part-time jobs and apprenticeships together.

One notification can carry both sources. `services/scout.py` (Reed/SerpAPI
jobs) and `services/apprenticeships.py` (gov.uk) both come through here, so
there is exactly one email system rather than one per source.

Transport is unchanged from the original Scout notifier: Gmail SMTP over
STARTTLS using SCOUT_EMAIL_USER / SCOUT_EMAIL_PASS, delivering to
NOTIFY_EMAIL_TO. Discord is optional and only fires when DISCORD_WEBHOOK_URL is
set. Best-effort throughout — a send failure is logged and swallowed, never
raised into a scan.

Scout has no alerts table; outcomes are recorded via db.log_audit, matching how
scout_daily_scan already reports itself.
"""
import logging
import os
import smtplib
import ssl
from email.message import EmailMessage

import requests

import database as db

logger = logging.getLogger("asfa.scout_notify")

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587
NOTIFY_EMAIL_TO = "ami.salax08@gmail.com"

JOB = "job"
APPRENTICESHIP = "apprenticeship"


# ── Transport ────────────────────────────────────────────────────────────────

def send_email(subject: str, html: str, text: str = None) -> tuple:
    """Send one HTML email. Returns (ok, error). Never raises."""
    user = os.environ.get("SCOUT_EMAIL_USER")
    password = os.environ.get("SCOUT_EMAIL_PASS")
    if not (user and password):
        msg = "SCOUT_EMAIL_USER/SCOUT_EMAIL_PASS not set"
        logger.info("scout_notify: %s — skipping email", msg)
        return False, msg

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = user
    msg["To"] = NOTIFY_EMAIL_TO
    msg.set_content(text or _html_to_text(html))
    msg.add_alternative(html, subtype="html")

    try:
        context = ssl.create_default_context()
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=20) as server:
            server.ehlo()
            server.starttls(context=context)
            server.ehlo()
            server.login(user, password)
            server.send_message(msg)
        logger.info("scout_notify: emailed %r to %s", subject, NOTIFY_EMAIL_TO)
        return True, ""
    except Exception as e:
        logger.warning("scout_notify: email send failed: %s", e)
        return False, f"{type(e).__name__}: {e}"


def send_discord(content: str, embeds=None) -> tuple:
    """Post to the Discord webhook if one is configured. Returns (ok, error)."""
    url = (os.environ.get("DISCORD_WEBHOOK_URL") or "").strip()
    if not url:
        return False, "Discord webhook not configured"
    payload = {"content": content[:2000]}
    if embeds:
        payload["embeds"] = embeds[:10]  # Discord's max
    try:
        r = requests.post(url, json=payload, timeout=15,
                          headers={"Content-Type": "application/json"})
        r.raise_for_status()
        return True, ""
    except requests.RequestException as e:
        logger.warning("scout_notify: discord post failed: %s", e)
        return False, f"{type(e).__name__}: {e}"


# ── Normalisation ────────────────────────────────────────────────────────────

def _as_row(item, kind: str) -> dict:
    """Flatten a job dict or a ScoutJob model into one shape the templates use.

    Jobs arrive from services/scout.py as plain dicts; apprenticeships arrive
    from services/apprenticeships.py as ScoutJob instances. Normalising here is
    what lets a single template render both.
    """
    if isinstance(item, dict):
        get = item.get
        employer = get("company") or ""
    else:
        get = lambda k, d=None: getattr(item, k, d)  # noqa: E731
        employer = getattr(item, "employer_display", None) or get("company") or ""
    return {
        "kind": kind,
        "title": get("title") or "Untitled",
        "employer": employer,
        "location": get("location") or "",
        "url": get("url") or "",
        "salary": get("salary") or "",
        "posted": get("posted_date") or "",
        "level": get("level"),
        "training_course": get("training_course") or "",
        "closing_text": get("closing_text") or "",
    }


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def build_subject(n_jobs: int, n_appr: int) -> str:
    """e.g. '🚨 3 new jobs, 2 new apprenticeships'. A zero category is omitted."""
    parts = []
    if n_jobs:
        parts.append(_plural(n_jobs, "new job"))
    if n_appr:
        parts.append(_plural(n_appr, "new apprenticeship"))
    if not parts:
        return "🚨 Scout — nothing new"
    return "🚨 " + ", ".join(parts)


# ── Rendering ────────────────────────────────────────────────────────────────

_BADGE = {
    JOB: ("JOB", "#1971c2"),
    APPRENTICESHIP: ("APPRENTICESHIP", "#0b7285"),
}


def _render_row(row: dict) -> str:
    label, colour = _BADGE[row["kind"]]
    meta = []
    if row["kind"] == APPRENTICESHIP:
        # The fields the decision actually gets made on.
        if row["level"]:
            meta.append(f"Level {row['level']}")
        if row["salary"]:
            meta.append(_esc(row["salary"]))
        if row["training_course"]:
            meta.append(_esc(row["training_course"]))
    else:
        if row["salary"]:
            meta.append(_esc(str(row["salary"])))
        if row["posted"]:
            meta.append("posted " + _esc(row["posted"]))

    closing = ""
    if row["closing_text"]:
        closing = (f'<div style="color:#c92a2a;font-size:12px;margin-top:6px;">'
                   f'{_esc(row["closing_text"])}</div>')

    return f"""
      <tr>
        <td style="padding:14px 12px;border-bottom:1px solid #e9ecef;vertical-align:top;">
          <span style="display:inline-block;background:{colour};color:#fff;font-size:10px;
                       font-weight:700;letter-spacing:.6px;padding:2px 6px;border-radius:3px;">
            {label}
          </span>
          <div style="font-weight:600;font-size:15px;margin-top:6px;">
            <a href="{_esc(row['url'])}" style="color:{colour};text-decoration:none;">{_esc(row['title'])}</a>
          </div>
          <div style="color:#495057;font-size:13px;margin-top:4px;">
            <strong>{_esc(row['employer'])}</strong>&nbsp;&middot;&nbsp;{_esc(row['location'])}
          </div>
          <div style="color:#868e96;font-size:12px;margin-top:6px;">{' &middot; '.join(meta)}</div>
          {closing}
        </td>
      </tr>
    """


def _render_group(heading: str, rows: list) -> str:
    if not rows:
        return ""
    return f"""
      <tr>
        <td style="padding:16px 12px 6px;font-family:monospace;font-size:11px;
                   letter-spacing:1.5px;color:#868e96;text-transform:uppercase;">
          {_esc(heading)} ({len(rows)})
        </td>
      </tr>
      {''.join(_render_row(r) for r in rows)}
    """


def render_email(jobs: list, apprenticeships: list, heading: str = None,
                 urgent: bool = False) -> str:
    """Build the combined HTML body, grouped by source."""
    accent = "#c92a2a" if urgent else "#1971c2"
    total = len(jobs) + len(apprenticeships)
    heading = heading or "New finds from Scout"
    return f"""<!doctype html>
<html>
  <body style="font-family:-apple-system,BlinkMacSystemFont,Segoe UI,Arial,sans-serif;
               background:#f8f9fa;margin:0;padding:24px;">
    <table cellpadding="0" cellspacing="0" width="100%" style="max-width:640px;margin:auto;
           background:#fff;border-radius:8px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,.05);">
      <tr>
        <td style="padding:20px 24px;background:{accent};color:#fff;">
          <div style="font-size:20px;font-weight:700;">{_esc(heading)}</div>
          <div style="font-size:13px;opacity:.85;margin-top:4px;">
            ASFA Scout &middot; {total} listing{'' if total == 1 else 's'}
          </div>
        </td>
      </tr>
      {_render_group("Jobs", jobs)}
      {_render_group("Apprenticeships", apprenticeships)}
      <tr>
        <td style="padding:16px 24px;color:#868e96;font-size:12px;">
          Sent by ASFA Scout. Manage the employer watchlist at /scout/employers.
        </td>
      </tr>
    </table>
  </body>
</html>"""


# ── High-level alerts ────────────────────────────────────────────────────────

def alert_new_listings(jobs=None, apprenticeships=None) -> bool:
    """Alert on newly-found jobs and/or apprenticeships in ONE notification.

    Callers pass whichever sources they have; a source with nothing new is
    simply omitted from the subject and body. Returns True if an email went out.
    """
    job_rows = [_as_row(j, JOB) for j in (jobs or [])]
    appr_rows = [_as_row(a, APPRENTICESHIP) for a in (apprenticeships or [])]
    if not (job_rows or appr_rows):
        return False

    subject = build_subject(len(job_rows), len(appr_rows))
    html = render_email(job_rows, appr_rows, urgent=True)
    ok, err = send_email(subject, html)
    _log("new_listings", subject, ok, err,
         {"jobs": len(job_rows), "apprenticeships": len(appr_rows)})

    embeds = [{
        "title": r["title"][:250],
        "url": r["url"],
        "description": (f"**{r['employer']}**\n"
                        f"📍 {r['location'] or 'n/a'}\n"
                        f"💰 {r['salary'] or 'n/a'}\n"
                        + (f"📚 {r['training_course']}\n⏰ {r['closing_text']}"
                           if r["kind"] == APPRENTICESHIP else ""))[:2000],
        "color": 0x0B7285 if r["kind"] == APPRENTICESHIP else 0x1971C2,
    } for r in (job_rows + appr_rows)[:10]]
    if embeds:
        send_discord(f"🚨 **{subject[2:].strip()}**", embeds)
    return ok


def alert_closing_soon(apprenticeships: list, days: int = 3) -> bool:
    """The once-daily reminder for apprenticeships about to close."""
    rows = [_as_row(a, APPRENTICESHIP) for a in (apprenticeships or [])]
    if not rows:
        return False
    n = len(rows)
    subject = (f"⏳ {_plural(n, 'apprenticeship')} closing "
               f"in ≤{days} day{'' if days == 1 else 's'}")
    html = render_email([], rows, heading=subject, urgent=True)
    ok, err = send_email(subject, html)
    _log("closing_soon", subject, ok, err, {"count": n, "days": days})
    return ok


def _log(kind: str, subject: str, ok: bool, err: str, details: dict) -> None:
    """Record the attempt in Scout's existing audit trail. Never raises."""
    try:
        db.log_audit("scout", f"alert_{kind}", "success" if ok else "failure",
                     reason=subject,
                     details=dict(details, error=err or None))
    except Exception as e:
        logger.warning("scout_notify: audit log failed: %s", e)


def _esc(text) -> str:
    return (str(text or "")
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;"))


def _html_to_text(html: str) -> str:
    """Bare-bones text/plain fallback."""
    import re
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip()
