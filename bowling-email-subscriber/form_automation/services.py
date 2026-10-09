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
from typing import Literal
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
    for preferences in (("plain", "html"), ("html",)):
        body_part = message.get_body(preferencelist=preferences)
        if body_part is not None:
            result = parse_dac_invite_content(
                str(message.get("From", "")),
                str(message.get("Subject", "")).strip(),
                body_part.get_content(),
            )
            if result is not None:
                return result
    return None


def parse_dac_invite_content(
    sender: str, subject: str, body: str
) -> tuple[str, date] | None:
    """Parse DAC invite metadata from already-decoded Gmail message fields."""
    if DAC_SENDER not in sender:
        return None
    subject = subject.strip()
    match = REQUEST_SUBJECT_RE.match(subject)
    if not match:
        return None
    month, day, year = (int(part) for part in match.groups())
    try:
        event_date = date(year, month, day)
    except ValueError:
        logger.warning("Unparseable event date in DAC subject: %r", subject)
        return None
    found = INVITE_URL_RE.search(body)
    if found:
        return found.group(0).rstrip(".,;)"), event_date
    logger.warning("DAC invite email had no accept URL (subject=%r)", subject)
    return None


def is_acceptable_date(event_date: date) -> bool:
    """Only accept invites for today or a future date (Detroit time)."""
    return event_date >= datetime.now(DETROIT).date()


def accept_invite(invite_url: str, *, timeout_ms: int = 30000) -> bool:
    """Open the invite URL headlessly, click Accept, verify confirmation."""
    return respond_to_invite(invite_url, "accept", timeout_ms=timeout_ms)


def decline_invite(invite_url: str, *, timeout_ms: int = 30000) -> bool:
    """Open the invite URL headlessly, click Decline, verify confirmation."""
    return respond_to_invite(invite_url, "decline", timeout_ms=timeout_ms)


def respond_to_invite(
    invite_url: str,
    action: Literal["accept", "decline"],
    *,
    timeout_ms: int = 30000,
) -> bool:
    """Follow a DAC invitation and submit the selected response.

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
            button = _find_response_button(page, action)
            if button is None:
                logger.error("No %s button found on the invite page", action.title())
                return False
            button.click(timeout=timeout_ms)
            page.wait_for_load_state("networkidle", timeout=timeout_ms)
            text = page.locator("body").inner_text(timeout=timeout_ms)
            response_word = "accepted" if action == "accept" else "declined"
            if re.search(
                rf"\b({response_word}|confirm|thank|success)\b", text, re.IGNORECASE
            ):
                logger.info("DAC invite %s (confirmation detected)", action)
                return True
            logger.warning(
                "%s clicked but no confirmation text detected", action.title()
            )
            return False
        finally:
            browser.close()


def _find_response_button(page, action: Literal["accept", "decline"]):
    """Return only the requested action's button or link."""
    name = re.compile(rf"^{re.escape(action)}$", re.IGNORECASE)
    attempts = [
        lambda: page.get_by_role("button", name=name),
        lambda: page.get_by_role("link", name=name),
        lambda: page.locator(f'input[type="submit"][value="{action.title()}"]'),
        lambda: page.locator(f'input[type="button"][value="{action.title()}"]'),
    ]
    for attempt in attempts:
        try:
            locator = attempt()
            if locator.count() > 0 and locator.first.is_visible():
                return locator.first
        except Exception as exc:  # noqa: BLE001 - try the next selector
            logger.debug("Accept-button selector failed: %s", exc)
            continue
    return None
