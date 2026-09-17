from gmail_subscriber.services import GmailSubscriberService


async def provide_gmail_subscriber_service() -> GmailSubscriberService:
    return GmailSubscriberService()
