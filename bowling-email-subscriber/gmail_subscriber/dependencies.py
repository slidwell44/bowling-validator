from functools import lru_cache

from gmail_subscriber.services import GmailSubscriberService


@lru_cache(maxsize=1)
def get_gmail_subscriber_service() -> GmailSubscriberService:
    return GmailSubscriberService()


async def provide_gmail_subscriber_service() -> GmailSubscriberService:
    return get_gmail_subscriber_service()
