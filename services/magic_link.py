"""Passwordless sign-in: email a one-time login link.

Transport mirrors scout_notify.py exactly — Gmail SMTP over STARTTLS using the
same SCOUT_EMAIL_USER/SCOUT_EMAIL_PASS app password already configured for
Scout's job alerts, so no new credentials are needed. Kept as its own module
(rather than added to scout_notify) because this is an auth concern, not a
Scout concern, even though the transport happens to be identical.
"""
import logging
import os
import smtplib
import ssl
from email.message import EmailMessage

import certifi

logger = logging.getLogger("asfa.magic_link")

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587


def send_magic_link_email(to_addr: str, link: str) -> tuple:
    """Send the sign-in link. Returns (ok, error). Never raises — a failed send
    should not crash the request; the caller always shows the same generic
    "check your email" response regardless (prevents email enumeration)."""
    user = os.environ.get("SCOUT_EMAIL_USER")
    password = os.environ.get("SCOUT_EMAIL_PASS")
    if not (user and password):
        msg = "SCOUT_EMAIL_USER/SCOUT_EMAIL_PASS not set"
        logger.warning("magic_link: %s — cannot send", msg)
        return False, msg

    text = (
        "Sign in to ASFA\n\n"
        f"Click this link to sign in:\n{link}\n\n"
        "This link expires in 15 minutes and can only be used once.\n"
        "If you didn't request this, you can ignore this email."
    )
    html = f"""\
<div style="font-family:-apple-system,sans-serif;max-width:480px;margin:0 auto;padding:32px 24px;">
  <h2 style="margin:0 0 16px;font-size:18px;">Sign in to ASFA</h2>
  <p style="color:#444;font-size:14px;line-height:1.5;">Click the button below to sign in. This link expires in 15 minutes and can only be used once.</p>
  <p style="margin:28px 0;">
    <a href="{link}" style="background:#00d9ff;color:#001318;padding:12px 24px;border-radius:8px;text-decoration:none;font-weight:700;font-size:14px;letter-spacing:.04em;">SIGN IN</a>
  </p>
  <p style="color:#888;font-size:12px;">If you didn't request this, you can ignore this email.</p>
</div>"""

    msg = EmailMessage()
    msg["Subject"] = "Your ASFA sign-in link"
    msg["From"] = user
    msg["To"] = to_addr
    msg.set_content(text)
    msg.add_alternative(html, subtype="html")

    try:
        # certifi's bundle explicitly, rather than the OS trust store — sidesteps
        # "unable to get local issuer certificate" on Python installs that don't
        # share the system CA store (common on local macOS dev).
        context = ssl.create_default_context(cafile=certifi.where())
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=20) as server:
            server.ehlo()
            server.starttls(context=context)
            server.ehlo()
            server.login(user, password)
            server.send_message(msg)
        logger.info("magic_link: sign-in link emailed to %s", to_addr)
        return True, ""
    except Exception as e:
        logger.warning("magic_link: email send failed: %s", e)
        return False, f"{type(e).__name__}: {e}"
