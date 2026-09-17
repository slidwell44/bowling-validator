from __future__ import annotations

from typing import TYPE_CHECKING

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from common.services import BaseService
from scripts.oauth import SCOPES, TOKEN_FILE

if TYPE_CHECKING:
    from googleapiclient._apis.gmail.v1 import GmailResource
    from googleapiclient._apis.gmail.v1.schemas import Label, ListLabelsResponse


class GmailSubscriberService(BaseService):
    def __init__(self) -> None:
        self._gmail_service: GmailResource = build(
            "gmail",
            "v1",
            credentials=self.credentials,
        )

    @property
    def credentials(self) -> Credentials:
        if not TOKEN_FILE.exists():
            raise RuntimeError(
                "Gmail is not authorized. Run: python -m gmail_subscriber.oauth"
            )
        credentials: Credentials = Credentials.from_authorized_user_file(
            str(TOKEN_FILE), SCOPES
        )
        if not credentials.refresh_token:
            raise RuntimeError(
                "Gmail refresh token is missing. Run: python -m gmail_subscriber.oauth"
            )
        return credentials

    async def fetch_gmail_labels(self) -> list[Label]:
        results: ListLabelsResponse = (
            self._gmail_service.users().labels().list(userId="me").execute()
        )
        labels: list[Label] = results.get("labels", [])
        return labels
