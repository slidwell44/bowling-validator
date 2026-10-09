from __future__ import annotations

import base64
import json
import logging
import secrets
import uuid
from contextlib import contextmanager
from email import policy
from email.header import decode_header, make_header
from email.parser import BytesParser
from threading import RLock
from typing import TYPE_CHECKING

from google.auth.transport.requests import Request
from google.oauth2 import id_token
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from config import GmailSettings
from form_automation.services import (
    accept_invite,
    is_acceptable_date,
    parse_dac_invite_content,
)
from gmail_subscriber.repositories import STATE_FILE, MailboxRepository
from scripts.oauth import SCOPES, TOKEN_FILE

if TYPE_CHECKING:
    from googleapiclient._apis.gmail.v1 import GmailResource
    from googleapiclient._apis.gmail.v1.schemas import Label, ListLabelsResponse


class GmailSubscriberService:
    def __init__(
        self, repository: MailboxRepository, gmail_service: GmailResource
    ) -> None:
        self.repository = repository
        self._gmail_service = gmail_service

    @staticmethod
    def create_gmail_service() -> GmailResource:
        return build(
            "gmail",
            "v1",
            credentials=GmailSubscriberService.load_credentials(),
            cache_discovery=False,
        )

    @staticmethod
    def load_credentials() -> Credentials:
        token_json = GmailSettings().TOKEN_JSON
        if token_json:
            return Credentials.from_authorized_user_info(
                json.loads(token_json.get_secret_value()), SCOPES
            )
        if not TOKEN_FILE.exists():
            raise RuntimeError(
                "Gmail is not authorized. Run: uv run python -m scripts.oauth"
            )
        credentials: Credentials = Credentials.from_authorized_user_file(
            str(TOKEN_FILE), SCOPES
        )
        if not credentials.refresh_token:
            raise RuntimeError(
                "Gmail refresh token is missing. Run: uv run python -m scripts.oauth"
            )
        return credentials

    @staticmethod
    def verify_identity(token: str, *, watch: bool = False) -> None:
        settings = GmailSettings()
        audience = settings.WATCH_AUDIENCE if watch else settings.PUSH_AUDIENCE
        email = (
            settings.WATCH_SERVICE_ACCOUNT if watch else settings.PUSH_SERVICE_ACCOUNT
        )
        if not audience or not email:
            raise RuntimeError("Google delivery identity settings are not configured")
        claims = id_token.verify_oauth2_token(token, Request(), audience=audience)
        if claims.get("email") != email or claims.get("email_verified") is not True:
            raise ValueError("Unexpected Google service account")

    @staticmethod
    def verify_admin(token: str) -> bool:
        configured = GmailSettings().ADMIN_TOKEN
        return bool(
            configured and secrets.compare_digest(token, configured.get_secret_value())
        )

    def start_watch(self):
        with _gmail_api_lock:
            return start_watch(
                self.gmail, GmailSettings().PUBSUB_TOPIC, self.repository
            )

    def process_notification(self, email: str, history_id: str):
        with _gmail_api_lock:
            return process_notification(self.gmail, email, history_id, self.repository)

    @property
    def gmail(self) -> GmailResource:
        return self._gmail_service

    @property
    def credentials(self) -> Credentials:
        return self.load_credentials()

    def fetch_gmail_labels(self) -> list[Label]:
        with _gmail_api_lock:
            results: ListLabelsResponse = (
                self._gmail_service.users()
                .labels()
                .list(userId="me")
                .execute(num_retries=GMAIL_HTTP_RETRIES)
            )
        labels: list[Label] = results.get("labels", [])
        return labels


class GmailQuotaExceeded(Exception):
    """Raised when Gmail rejects a request for exceeding its query quota."""


class GmailProcessingBusy(Exception):
    """Raised when another replica currently owns this mailbox's processing lease."""


SUBJECT_MARKER = "Substitute bowler request"
TEST_SUBJECT = "Test"
GMAIL_HTTP_RETRIES = 3
logger = logging.getLogger(__name__)
_gmail_api_lock = RLock()


def is_relevant_subject(subject: str) -> bool:
    normalized = subject.casefold()
    return (
        normalized == TEST_SUBJECT.casefold() or SUBJECT_MARKER.casefold() in normalized
    )


def matching_body(raw: str) -> str | None:
    message = BytesParser(policy=policy.default).parsebytes(
        base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))
    )
    if not is_relevant_subject(str(message.get("Subject", ""))):
        return None
    body = message.get_body(preferencelist=("plain", "html"))
    return body.get_content() if body is not None else ""


def decoded_subject(value: str) -> str:
    return str(make_header(decode_header(value)))


def is_gmail_quota_error(error: HttpError) -> bool:
    if error.resp.status != 403:
        return False
    try:
        payload = json.loads(error.content.decode("utf-8"))
    except AttributeError, UnicodeDecodeError, json.JSONDecodeError:
        return "quota exceeded" in str(error).casefold()
    details = payload.get("error", {})
    reasons = {item.get("reason", "") for item in details.get("errors", [])}
    return bool(
        reasons.intersection(
            {"rateLimitExceeded", "userRateLimitExceeded", "quotaExceeded"}
        )
        or "quota exceeded" in details.get("message", "").casefold()
    )


def message_subject_and_body(message: dict) -> tuple[str, str]:
    payload = message.get("payload", {})
    subject = decoded_subject(
        next(
            (
                header["value"]
                for header in payload.get("headers", [])
                if header.get("name", "").lower() == "subject"
            ),
            "",
        )
    )
    text_parts: list[tuple[str, bytes, str]] = []

    def collect_parts(part: dict) -> None:
        mime_type = part.get("mimeType", "")
        data = part.get("body", {}).get("data")
        if mime_type in {"text/plain", "text/html"} and data:
            part_headers = {
                header.get("name", "").lower(): header.get("value", "")
                for header in part.get("headers", [])
            }
            charset = "utf-8"
            content_type = part_headers.get("content-type", "")
            for parameter in content_type.split(";")[1:]:
                name, separator, value = parameter.strip().partition("=")
                if separator and name.lower() == "charset":
                    charset = value.strip('"')
                    break
            decoded = base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))
            text_parts.append((mime_type, decoded, charset))
        for child in part.get("parts", []):
            collect_parts(child)

    collect_parts(payload)
    for preferred_type in ("text/plain", "text/html"):
        for mime_type, data, charset in text_parts:
            if mime_type == preferred_type:
                try:
                    return subject, data.decode(charset)
                except LookupError, UnicodeDecodeError:
                    return subject, data.decode("utf-8", errors="replace")
    return subject, ""


@contextmanager
def history_state(path=STATE_FILE):
    with MailboxRepository.from_settings(path) as repository:
        yield repository.connection


def start_watch(
    gmail,
    topic: str,
    repository=None,
    *,
    reset_history=False,
    path=STATE_FILE,
):
    if repository is None:
        with MailboxRepository.from_settings(path) as repository_context:
            return start_watch(
                gmail, topic, repository_context, reset_history=reset_history
            )
    email = (
        gmail.users()
        .getProfile(userId="me")
        .execute(num_retries=GMAIL_HTTP_RETRIES)["emailAddress"]
    )
    result = (
        gmail.users()
        .watch(
            userId="me",
            body={
                "topicName": topic,
                "labelIds": ["INBOX"],
                "labelFilterBehavior": "include",
            },
        )
        .execute(num_retries=GMAIL_HTTP_RETRIES)
    )
    if reset_history:
        repository.reset_history(email)
    repository.save_initial_history(email, result["historyId"])
    repository.save_watch_expiration(email, int(result["expiration"]))
    return result


def process_notification(
    gmail,
    email: str,
    history_id: str,
    repository=None,
    *,
    path=STATE_FILE,
):
    if repository is None:
        with MailboxRepository.from_settings(path) as repository_context:
            return process_notification(gmail, email, history_id, repository_context)
    lease_token = uuid.uuid4().hex
    if not repository.acquire_processing_lease(email, lease_token):
        raise GmailProcessingBusy("Mailbox is already processing or in quota cooldown")
    try:
        return _process_notification_with_repository(
            gmail, email, history_id, repository
        )
    except GmailQuotaExceeded:
        try:
            repository.defer_processing_lease(email, lease_token, delay_seconds=60)
        except Exception:
            logger.exception("Could not persist Gmail quota cooldown")
        raise
    finally:
        repository.release_processing_lease(email, lease_token)


def _process_notification_with_repository(
    gmail, email: str, history_id: str, repository: MailboxRepository
):
    # Persist progress only after every history page succeeds.
    cursor = repository.get_history_id(email)
    if cursor is None:
        raise RuntimeError(
            "Mailbox watch is not initialized. Run python -m scripts.watch."
        )
    if int(history_id) <= int(cursor):
        logger.info(
            "Skipping Gmail history %s for %s; cursor is already %s",
            history_id,
            email,
            cursor,
        )
        return
    logger.info(
        "Processing Gmail history %s for %s from cursor %s",
        history_id,
        email,
        cursor,
    )
    checkpoint = repository.get_history_checkpoint(email)
    if checkpoint is None:
        start_history_id = cursor
        page_token = None
    else:
        start_history_id, page_token = checkpoint
    seen = set()
    while True:
        try:
            page = (
                gmail.users()
                .history()
                .list(
                    userId="me",
                    startHistoryId=start_history_id,
                    historyTypes=["messageAdded"],
                    pageToken=page_token,
                )
                .execute(num_retries=GMAIL_HTTP_RETRIES)
            )
        except HttpError as exc:
            if exc.resp.status == 404:
                logger.error(
                    "Gmail history expired. Run python -m scripts.watch --reset-history "
                    "to resume from now; intervening messages will not be replayed."
                )
            if is_gmail_quota_error(exc):
                raise GmailQuotaExceeded(
                    "Gmail query quota exceeded while listing mailbox history"
                ) from exc
            raise
        for event in page.get("history", []):
            for added in event.get("messagesAdded", []):
                listed_message = added["message"]
                message_id = listed_message["id"]
                if message_id in seen or "INBOX" not in listed_message.get(
                    "labelIds", []
                ):
                    continue
                seen.add(message_id)
                if repository.was_message_inspected(email, message_id):
                    continue
                try:
                    message = (
                        gmail.users()
                        .messages()
                        .get(userId="me", id=message_id, format="full")
                        .execute(num_retries=GMAIL_HTTP_RETRIES)
                    )
                except HttpError as exc:
                    if exc.resp.status == 404:
                        repository.mark_message_inspected(email, message_id)
                        continue  # The message was deleted before delivery.
                    if is_gmail_quota_error(exc):
                        raise GmailQuotaExceeded(
                            "Gmail query quota exceeded while reading a message"
                        ) from exc
                    raise
                subject, body = message_subject_and_body(message)
                if not is_relevant_subject(subject):
                    logger.info(
                        "Skipping message %s with non-matching subject %r",
                        message_id,
                        subject,
                    )
                if is_relevant_subject(subject):
                    inserted = repository.record_processed_message(
                        message_id, email, subject, body, history_id
                    )
                    if inserted:
                        print(
                            f"\n--- Gmail message {message_id}: {subject} ---\n{body}\n",
                            flush=True,
                        )
                    invite = parse_dac_invite_content(
                        _message_header(message, "from"), subject, body
                    )
                    if invite is not None:
                        invite_url, event_date = invite
                        if not is_acceptable_date(event_date):
                            logger.info(
                                "Skipping DAC invite for past date %s (%s)",
                                event_date,
                                message_id,
                            )
                        elif repository.was_invite_accepted(event_date.isoformat()):
                            logger.info(
                                "DAC invite date %s already reserved or accepted; "
                                "skipping message %s",
                                event_date,
                                message_id,
                            )
                        elif not repository.reserve_invite_date(
                            event_date.isoformat(), message_id
                        ):
                            logger.info(
                                "Another invite already reserved date %s; skipping %s",
                                event_date,
                                message_id,
                            )
                        else:
                            logger.info(
                                "Auto-accepting DAC invite for %s (%s)",
                                event_date,
                                message_id,
                            )
                            if accept_invite(invite_url):
                                repository.record_accepted_invite(
                                    event_date.isoformat(), message_id
                                )
                            else:
                                raise RuntimeError(
                                    f"DAC invite {message_id} accepted-click did "
                                    "not confirm; leaving cursor so delivery retries"
                                )
                repository.mark_message_inspected(email, message_id)
        next_page_token = page.get("nextPageToken")
        if next_page_token:
            repository.save_history_checkpoint(email, start_history_id, next_page_token)
            page_token = next_page_token
            continue
        repository.complete_history_sync(email, page["historyId"])
        logger.info(
            "Advanced Gmail history cursor for %s to %s",
            email,
            page["historyId"],
        )
        break


def _message_header(message: dict, header_name: str) -> str:
    return next(
        (
            header.get("value", "")
            for header in message.get("payload", {}).get("headers", [])
            if header.get("name", "").lower() == header_name.lower()
        ),
        "",
    )
