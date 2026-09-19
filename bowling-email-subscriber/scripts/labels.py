import json

from gmail_subscriber.repositories import MailboxRepository
from gmail_subscriber.services import GmailSubscriberService


def main() -> None:
    with MailboxRepository.from_settings() as repository:
        service = GmailSubscriberService(
            repository, GmailSubscriberService.create_gmail_service()
        )
        labels = service.fetch_gmail_labels()
    print(json.dumps(labels, indent=2))


if __name__ == "__main__":
    main()
