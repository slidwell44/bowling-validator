"""Automated acceptance of DAC substitute-bowler invitations.

Pipeline (invoked from the Gmail subscriber when a DAC email arrives):

1. :func:`parse_dac_invite` detects DAC sub-request emails
   (``From: dacmail@dacrsc.com``, subject
   ``Substitute bowler request for <name>; MM/DD/YYYY``) and extracts the
   "accept or decline" URL from the message body.
2. :func:`accept_invite` opens that URL in headless Chromium (Playwright),
   clicks the Accept button, and verifies the confirmation page.

Safety rules:

- Only ``Substitute bowler request`` emails are handled; updates and
  cancellations are ignored.
- The open-tracking pixel (``/o/`` URLs) is never followed -- only the
  invite link (``/c/`` URLs).
- Invites for past dates are skipped.
- Callers record each accepted Gmail message ID so a Pub/Sub redelivery
  never double-accepts.

Requires Playwright's Chromium on the host::

    uv run playwright install chromium
"""

from __future__ import annotations

import base64
import logging
import re
from datetime import date, datetime
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

DAC_SENDER = "dacmail@dacrsc.com"
REQUEST_SUBJECT_RE = re.compile(
    r"^Substitute bowler request for .+; (\d{2})/(\d{2})/(\d{4})$"
)
# /c/ is the invite tracking link; /o/ is the open-tracking pixel.
INVITE_URL_RE = re.compile(r"https?://[^\s\"'<>]+/c/[^\s\"'<>]+")
DETROIT = ZoneInfo("America/Detroit")


def parse_dac_invite(raw: str) -> tuple[str, date] | None:
    """Return ``(invite_url, event_date)`` for a DAC sub-request email.

    ``raw`` is the Gmail API ``raw`` message payload (URL-safe base64).
    Returns ``None`` for anything that is not a DAC sub-request.
    """
    message: EmailMessage = BytesParser(policy=policy.default).parsebytes(
        base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))
    )
    if DAC_SENDER not in str(message.get("From", "")):
        return None
    subject = str(message.get("Subject", "")).strip()
    match = REQUEST_SUBJECT_RE.match(subject)
    if not match:
        return None
    month, day, year = (int(part) for part in match.groups())
    try:
        event_date = date(year, month, day)
    except ValueError:
        logger.warning("Unparseable event date in DAC subject: %r", subject)
        return None
    for preferences in (("plain", "html"), ("html",)):
        body = message.get_body(preferencelist=preferences)
        if body is None:
            continue
        found = INVITE_URL_RE.search(body.get_content())
        if found:
            return found.group(0).rstrip(".,;)"), event_date
    logger.warning("DAC invite email had no accept URL (subject=%r)", subject)
    return None


def is_acceptable_date(event_date: date) -> bool:
    """Only accept invites for today or a future date (Detroit time)."""
    return event_date >= datetime.now(DETROIT).date()


def accept_invite(invite_url: str, *, timeout_ms: int = 30000) -> bool:
    """Open the invite URL headlessly, click Accept, verify confirmation.

    Returns ``True`` when the resulting page looks like a confirmation.
    Raises on navigation or click failures so the caller can retry later.
    """
    from playwright.sync_api import sync_playwright

    netloc = urlparse(invite_url).netloc
    logger.info("Opening DAC invite page (%s)", netloc)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            # Auto-confirm any JavaScript confirm() dialog the site shows.
            page.on("dialog", lambda dialog: dialog.accept())
            page.goto(invite_url, wait_until="domcontentloaded", timeout=timeout_ms)
            # The tracking link redirects; let the landing page settle.
            page.wait_for_load_state("networkidle", timeout=timeout_ms)
            button = _find_accept_button(page)
            if button is None:
                logger.error("No Accept button found on the invite page")
                return False
            button.click(timeout=timeout_ms)
            page.wait_for_load_state("networkidle", timeout=timeout_ms)
            text = page.locator("body").inner_text(timeout=timeout_ms)
            if re.search(r"\b(accept|confirm|thank|success)\b", text, re.IGNORECASE):
                logger.info("DAC invite accepted (confirmation detected)")
                return True
            logger.warning("Accept clicked but no confirmation text detected")
            return False
        finally:
            browser.close()


def _find_accept_button(page):  # type: ignore[no-untyped-def]
    """Return a locator for the Accept button, never the Decline one."""
    attempts = [
        lambda: page.get_by_role("button", name=re.compile(r"^accept$", re.I)),
        lambda: page.get_by_role("link", name=re.compile(r"^accept$", re.I)),
        lambda: page.locator('input[type="submit"][value="Accept"]'),
        lambda: page.locator('input[type="button"][value="Accept"]'),
    ]
    for attempt in attempts:
        try:
            locator = attempt()
            if locator.count() > 0 and locator.first.is_visible():
                return locator.first
        except Exception:  # noqa: BLE001 - try the next selector
            continue
    return None
