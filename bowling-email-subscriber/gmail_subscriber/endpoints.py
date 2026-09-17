from typing import Annotated

from fastapi import APIRouter, Depends

from gmail_subscriber.dependencies import provide_gmail_subscriber_service
from gmail_subscriber.services import GmailSubscriberService

router = APIRouter(prefix="/gmail-subscriber")


@router.get("/labels")
async def fetch_gmail_labels(
    service: Annotated[
        GmailSubscriberService, Depends(provide_gmail_subscriber_service)
    ],
):
    return await service.fetch_gmail_labels()
