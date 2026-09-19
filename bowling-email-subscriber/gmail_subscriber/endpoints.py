import base64
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Response
from google.auth.exceptions import GoogleAuthError
from pydantic import BaseModel, Field, field_validator

from gmail_subscriber.dependencies import provide_gmail_subscriber_service
from gmail_subscriber.services import GmailSubscriberService

router = APIRouter(prefix="/gmail-subscriber")
logger = logging.getLogger(__name__)


class PubSubMessage(BaseModel):
    data: str


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
):
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
    service = GmailSubscriberService()
    service.process_notification(notification.emailAddress, notification.historyId)
    logger.info("Processed Gmail push notification for %s", notification.emailAddress)
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
