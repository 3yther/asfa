"""Multi-channel proactive notifications.

`send_alert()` fans a message out to every configured channel — always the
in-app bell, plus Telegram / Discord / email when their env vars are set. Each
channel is independent and failures are swallowed so one dead channel can't
break the others or the scheduler.

Env vars (all optional):
  DISCORD_WEBHOOK_URL
  SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, SUMMARY_EMAIL_TO, SUMMARY_EMAIL_FROM
"""
import logging
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import requests

import database as db
from services import telegram_bot

logger = logging.getLogger("asfa.alerts")


def _send_discord(message: str):
    url = os.environ.get("DISCORD_WEBHOOK_URL", "")
    if not url:
        return
    try:
        requests.post(url, json={"content": message[:1900]}, timeout=8)
    except Exception as e:
        logger.warning("Discord send failed: %s", e)


def email_configured() -> bool:
    return bool(os.environ.get("SMTP_HOST") and os.environ.get("SUMMARY_EMAIL_TO"))


def smtp_configured() -> bool:
    """SMTP alone, without requiring SUMMARY_EMAIL_TO. Senders that carry their
    own recipient (e.g. the weekly CSV export) check this instead."""
    return bool(os.environ.get("SMTP_HOST"))


def _smtp_send(msg, from_addr: str, to_addr: str) -> bool:
    """Open an SMTP session, STARTTLS + auth where available, send. Returns
    True on success; logs and returns False on any failure."""
    host = os.environ["SMTP_HOST"]
    port = int(os.environ.get("SMTP_PORT", 587))
    user = os.environ.get("SMTP_USER", "")
    password = os.environ.get("SMTP_PASSWORD", "")
    try:
        with smtplib.SMTP(host, port, timeout=30) as server:
            server.ehlo()
            try:
                server.starttls()
                server.ehlo()
            except smtplib.SMTPException:
                pass  # server without STARTTLS (e.g. local relay)
            if user and password:
                server.login(user, password)
            server.sendmail(from_addr, [to_addr], msg.as_string())
        return True
    except Exception as e:
        logger.warning("Email send failed: %s", e)
        return False


def send_email_with_attachments(subject: str, body: str, attachments,
                                to_addr: str = None) -> bool:
    """Send a plain-text email with file attachments.

    `attachments` is a sequence of (filename, text) pairs; each is attached as
    UTF-8 text/csv. Returns True on success.
    """
    if not smtp_configured():
        return False
    user = os.environ.get("SMTP_USER", "")
    to_addr = to_addr or os.environ.get("SUMMARY_EMAIL_TO", "")
    if not to_addr:
        logger.warning("No recipient for %r; skipping send.", subject)
        return False
    from_addr = os.environ.get("SUMMARY_EMAIL_FROM", user or to_addr)

    msg = MIMEMultipart()
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = to_addr
    msg.attach(MIMEText(body, "plain", "utf-8"))
    for filename, content in attachments:
        # text/csv (not application/csv) so mail clients offer an inline preview.
        part = MIMEText(content, "csv", "utf-8")
        part.add_header("Content-Disposition", "attachment", filename=filename)
        msg.attach(part)
    return _smtp_send(msg, from_addr, to_addr)


def send_email(subject: str, body: str) -> bool:
    """Send a plain-text email via SMTP. Returns True on success."""
    if not email_configured():
        return False
    user = os.environ.get("SMTP_USER", "")
    to_addr = os.environ["SUMMARY_EMAIL_TO"]
    from_addr = os.environ.get("SUMMARY_EMAIL_FROM", user or to_addr)
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = to_addr
    return _smtp_send(msg, from_addr, to_addr)


def send_alert(message: str, kind: str = "alert", subject: str = None,
               email: bool = False):
    """Fan a message out to all configured channels.

    in-app bell + Telegram always; Discord if a webhook is set; email only when
    `email=True` and SMTP is configured (used for the daily summary).
    """
    try:
        db.add_notification(message, kind)
    except Exception as e:
        logger.error("notification store failed: %s", e)

    try:
        telegram_bot.send_message(message)
    except Exception as e:
        logger.warning("Telegram send failed: %s", e)

    _send_discord(message)

    if email:
        send_email(subject or "ASFA Alert", message)
