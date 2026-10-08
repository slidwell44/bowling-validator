import argparse
from datetime import UTC, datetime

from config import GmailSettings
from gmail_subscriber.repositories import MailboxRepository
from gmail_subscriber.services import GmailSubscriberService, start_watch


def main():
    parser = argparse.ArgumentParser(
        description="Start or renew Gmail inbox notifications"
    )
    parser.add_argument(
        "--reset-history",
        action="store_true",
        help="Discard saved progress and start from now",
    )
    args = parser.parse_args()
    with MailboxRepository.from_settings() as repository:
        result = start_watch(
            GmailSubscriberService.create_gmail_service(),
            GmailSettings().PUBSUB_TOPIC,
            repository,
            reset_history=args.reset_history,
        )
    expiration = datetime.fromtimestamp(int(result["expiration"]) / 1000, UTC)
    print(
        f"Gmail watch active until {expiration.isoformat()}. Renew daily with this command."
    )


if __name__ == "__main__":
    main()
