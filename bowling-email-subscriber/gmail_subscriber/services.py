from google.oauth2.credentials import Credentials

from common.services import BaseService
from config import Settings, get_settings

settings: Settings = get_settings()


class GmailSubscriberService(BaseService):
    def __init__(self) -> None: ...

    @property
    def credentials(self) -> Credentials:
        return Credentials.from_authorized_user_info(
            {
                "client_id": settings.google.CLIENT_ID,
                "client_secret": settings.google.CLIENT_SECRET.get_secret_value(),
            },
        )

    async def fetch_gmail_labels(self): ...
