from __future__ import annotations

import base64
import json
import logging
import secrets
import sqlite3
from contextlib import contextmanager
from email import policy
from email.parser import BytesParser
from pathlib import Path
from typing import TYPE_CHECKING

import psycopg
from google.auth.transport.requests import Request
from google.oauth2 import id_token
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from config import GmailSettings
from gmail_subscriber.repositories import MailboxRepository
from scripts.oauth import SCOPES, TOKEN_FILE

if TYPE_CHECKING:
    from googleapiclient._apis.gmail.v1 import GmailResource
    from googleapiclient._apis.gmail.v1.schemas import Label, ListLabelsResponse


class GmailSubscriberService:
    def __init__(self) -> None:
        self._gmail_service: GmailResource = build(
            "gmail",
            "v1",
            credentials=self.credentials,
            cache_discovery=False,
        )

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
        return start_watch(self.gmail, GmailSettings().PUBSUB_TOPIC)

    def process_notification(self, email: str, history_id: str):
        return process_notification(self.gmail, email, history_id)

    @property
    def gmail(self) -> GmailResource:
        return self._gmail_service

    @property
    def credentials(self) -> Credentials:
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

    def fetch_gmail_labels(self) -> list[Label]:
        results: ListLabelsResponse = (
            self._gmail_service.users().labels().list(userId="me").execute()
        )
        labels: list[Label] = results.get("labels", [])
        return labels


STATE_FILE = Path(__file__).resolve().parents[1] / "gmail-history.sqlite3"
logger = logging.getLogger(__name__)


def matching_body(raw: str) -> str | None:
    message = BytesParser(policy=policy.default).parsebytes(
        base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))
    )
    if str(message.get("Subject", "")) != "Test":
        return None
    body = message.get_body(preferencelist=("plain", "html"))
    return body.get_content() if body is not None else ""


@contextmanager
def history_state(path=STATE_FILE):
    settings = GmailSettings()
    database_url = settings.DATABASE_URL
    if settings.TOKEN_JSON and not database_url:
        raise RuntimeError("DATABASE_URL is required with cloud Gmail credentials")
    connection = (
        psycopg.connect(database_url.get_secret_value(), connect_timeout=10)
        if database_url
        else sqlite3.connect(path, timeout=30)
    )
    try:
        if database_url:
            # Serialize watch registration and deliveries across cloud instances.
            connection.execute("SELECT pg_advisory_xact_lock(724938201)")
        MailboxRepository(connection).create_tables()
        if not database_url:
            connection.execute("BEGIN IMMEDIATE")
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def start_watch(gmail, topic: str, *, reset_history=False, path=STATE_FILE):
    with history_state(path) as state:
        repository = MailboxRepository(state)
        email = gmail.users().getProfile(userId="me").execute()["emailAddress"]
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
            .execute()
        )
        if reset_history:
            repository.reset_history(email)
        repository.save_initial_history(email, result["historyId"])
    return result


def process_notification(gmail, email: str, history_id: str, *, path=STATE_FILE):
    # Serialize deliveries and persist progress only after every page succeeds.
    with history_state(path) as state:
        repository = MailboxRepository(state)
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
        page_token = None
        seen = set()
        while True:
            try:
                page = (
                    gmail.users()
                    .history()
                    .list(
                        userId="me",
                        startHistoryId=cursor,
                        historyTypes=["messageAdded"],
                        pageToken=page_token,
                    )
                    .execute()
                )
            except HttpError as exc:
                if exc.resp.status == 404:
                    logger.error(
                        "Gmail history expired. Run python -m scripts.watch --reset-history "
                        "to resume from now; intervening messages will not be replayed."
                    )
                raise
            for event in page.get("history", []):
                for added in event.get("messagesAdded", []):
                    message = added["message"]
                    message_id = message["id"]
                    if message_id in seen or "INBOX" not in message.get("labelIds", []):
                        continue
                    seen.add(message_id)
                    try:
                        metadata = (
                            gmail.users()
                            .messages()
                            .get(
                                userId="me",
                                id=message_id,
                                format="metadata",
                                metadataHeaders=["Subject"],
                            )
                            .execute()
                        )
                    except HttpError as exc:
                        if exc.resp.status == 404:
                            continue  # The message was deleted before delivery.
                        raise
                    subject = next(
                        (
                            header["value"]
                            for header in metadata.get("payload", {}).get("headers", [])
                            if header.get("name", "").lower() == "subject"
                        ),
                        "",
                    )
                    if subject != "Test":
                        continue
                    try:
                        raw = (
                            gmail.users()
                            .messages()
                            .get(userId="me", id=message_id, format="raw")
                            .execute()["raw"]
                        )
                    except HttpError as exc:
                        if exc.resp.status == 404:
                            continue  # The message was deleted before delivery.
                        raise
                    body = matching_body(raw)
                    if body is not None and repository.record_processed_message(
                        message_id, email, subject, body, history_id
                    ):
                        print(
                            f"\n--- Gmail message {message_id}: Test ---\n{body}\n",
                            flush=True,
                        )
            page_token = page.get("nextPageToken")
            if not page_token:
                repository.update_history(email, page["historyId"])
                logger.info(
                    "Advanced Gmail history cursor for %s to %s",
                    email,
                    page["historyId"],
                )
                break
