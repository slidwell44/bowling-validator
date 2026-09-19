import asyncio
import json

from gmail_subscriber.services import GmailSubscriberService


async def main() -> None:
    service = GmailSubscriberService()
    labels = await service.fetch_gmail_labels()
    print(json.dumps(labels, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
