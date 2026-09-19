import json

from gmail_subscriber.services import GmailSubscriberService


def main() -> None:
    service = GmailSubscriberService()
    labels = service.fetch_gmail_labels()
    print(json.dumps(labels, indent=2))


if __name__ == "__main__":
    main()
