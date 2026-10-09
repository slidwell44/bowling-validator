import base64
import logging
import time
from datetime import UTC, datetime
from typing import Annotated

import psycopg
from fastapi import APIRouter, Depends, Header, HTTPException, Response
from google.auth.exceptions import GoogleAuthError
from pydantic import BaseModel, Field, field_validator

from config import GmailSettings
from gmail_subscriber.dependencies import provide_gmail_subscriber_service
from gmail_subscriber.services import (
    GmailProcessingBusy,
    GmailQuotaExceeded,
    GmailSubscriberService,
)

router = APIRouter(prefix="/gmail-subscriber")
logger = logging.getLogger(__name__)


class PubSubMessage(BaseModel):
    data: str
    message_id: str | None = Field(default=None, alias="messageId")


class PubSubEnvelope(BaseModel):
    message: PubSubMessage


class GmailNotification(BaseModel):
    emailAddress: str
    historyId: str = Field(pattern=r"^[0-9]+$")

    @field_validator("historyId", mode="before")
    @classmethod
    def normalize_history_id(cls, value):
        return str(value)


def bearer_token(authorization: str = Header(default="")) -> str:
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(401, "Missing bearer token")
    return token


def require_push_identity(token: Annotated[str, Depends(bearer_token)]):
    verify_identity(token)


def require_watch_identity(token: Annotated[str, Depends(bearer_token)]):
    verify_identity(token, watch=True)


def verify_identity(token: str, *, watch=False):
    try:
        GmailSubscriberService.verify_identity(token, watch=watch)
    except RuntimeError as exc:
        raise HTTPException(503, "Delivery identity is not configured") from exc
    except (ValueError, GoogleAuthError) as exc:
        raise HTTPException(401, "Invalid Google delivery identity") from exc


def require_admin(token: Annotated[str, Depends(bearer_token)]):
    if not GmailSubscriberService.verify_admin(token):
        raise HTTPException(403, "Invalid admin token")


@router.get("/health/db")
def database_health():
    """Unauthenticated liveness probe for the configured database.

    Returns the backend, the tables that exist, and whether DATABASE_URL is
    configured so a missing secret or unreachable Neon is visible without
    reading logs.
    """
    from gmail_subscriber.dependencies import provide_mailbox_repository

    repository_gen = provide_mailbox_repository()
    repository = next(repository_gen)
    try:
        connection = repository.connection
        is_postgres = isinstance(connection, psycopg.Connection)
        if is_postgres:
            rows = connection.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'public' ORDER BY table_name"
            ).fetchall()
            tables = [row[0] for row in rows]
        else:
            rows = connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            ).fetchall()
            tables = [row[0] for row in rows]

        mailbox = None
        leases = []
        processed_count = None
        last_processed = None
        watch_expiration_ms = None
        try:
            mailbox = connection.execute(
                "SELECT email, history_id FROM mailbox"
            ).fetchall()
            leases = connection.execute(
                "SELECT mailbox_email, lease_token, lease_until "
                "FROM mailbox_processing_leases"
            ).fetchall()
            row = connection.execute(
                "SELECT COUNT(*), MAX(processed_at) FROM processed_messages"
            ).fetchone()
            processed_count, last_processed = row[0], row[1]
            if mailbox:
                watch_expiration_ms = repository.get_watch_expiration(mailbox[0][0])
        except Exception:
            logger.exception("Could not read mailbox diagnostics")

        now_ms = int(time.time() * 1000)
        watch_expires_at = (
            datetime.fromtimestamp(watch_expiration_ms / 1000, UTC).isoformat()
            if watch_expiration_ms
            else None
        )
        watch_seconds_remaining = (
            max(0, (watch_expiration_ms - now_ms) // 1000)
            if watch_expiration_ms
            else None
        )
        # Healthy if the watch won't expire within a day and mail was processed
        # (or at least the watch is fresh) recently.
        stale = watch_seconds_remaining is None or watch_seconds_remaining < 24 * 3600

        return {
            "backend": "postgres" if is_postgres else "sqlite",
            "database_url_configured": GmailSettings().DATABASE_URL is not None,
            "tables": tables,
            "mailbox_cursor": [
                {"email": r[0], "history_id": r[1]} for r in (mailbox or [])
            ],
            "active_leases": [
                {"mailbox": r[0], "token": r[1], "until": r[2]} for r in (leases or [])
            ],
            "processed_count": processed_count,
            "last_processed_at": (
                str(last_processed) if last_processed is not None else None
            ),
            "watch_expires_at": watch_expires_at,
            "watch_seconds_remaining": watch_seconds_remaining,
            "stale": stale,
        }
    finally:
        repository_gen.close()


@router.get("/labels", dependencies=[Depends(require_admin)])
def fetch_gmail_labels(
    service: Annotated[
        GmailSubscriberService, Depends(provide_gmail_subscriber_service)
    ],
):
    return service.fetch_gmail_labels()


@router.post("/push", status_code=204, dependencies=[Depends(require_push_identity)])
def receive_push(
    envelope: PubSubEnvelope,
    service: Annotated[
        GmailSubscriberService, Depends(provide_gmail_subscriber_service)
    ],
):
    delivery_id = envelope.message.message_id or "unknown"
    logger.info("Received authenticated Pub/Sub delivery %s", delivery_id)
    try:
        notification = GmailNotification.model_validate_json(
            base64.urlsafe_b64decode(
                envelope.message.data + "=" * (-len(envelope.message.data) % 4)
            )
        )
    except ValueError as exc:
        logger.warning("Invalid Gmail notification payload: %s", exc)
        raise HTTPException(400, "Invalid Gmail notification") from exc
    logger.info(
        "Received Gmail push notification for %s at history %s",
        notification.emailAddress,
        notification.historyId,
    )
    try:
        service.process_notification(notification.emailAddress, notification.historyId)
    except GmailProcessingBusy as exc:
        logger.info(
            "Deferring Pub/Sub delivery %s: mailbox processing is active",
            delivery_id,
        )
        raise HTTPException(
            503,
            "Mailbox processing is already active; retry later",
            headers={"Retry-After": "30"},
        ) from exc
    except GmailQuotaExceeded as exc:
        logger.warning(
            "Deferring Pub/Sub delivery %s: Gmail quota reached; retry after 60s",
            delivery_id,
        )
        raise HTTPException(
            503,
            "Gmail quota temporarily exceeded; retry later",
            headers={"Retry-After": "60"},
        ) from exc
    logger.info("Processed Gmail push notification for %s", notification.emailAddress)
    logger.info("Acknowledging Pub/Sub delivery %s with HTTP 204", delivery_id)
    return Response(status_code=204)


@router.post("/watch", dependencies=[Depends(require_watch_identity)])
def renew_watch(
    service: Annotated[
        GmailSubscriberService, Depends(provide_gmail_subscriber_service)
    ],
):
    result = service.start_watch()
    logger.info(
        "Registered Gmail watch at history %s; expiration %s",
        result.get("historyId"),
        result.get("expiration"),
    )
    return result
