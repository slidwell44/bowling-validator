from collections.abc import Iterator
from functools import lru_cache
from typing import Annotated, Any

from fastapi import Depends

from gmail_subscriber.repositories import MailboxRepository
from gmail_subscriber.services import GmailSubscriberService


@lru_cache(maxsize=1)
def get_gmail_service():
    return GmailSubscriberService.create_gmail_service()


def provide_mailbox_repository() -> Iterator[MailboxRepository]:
    with MailboxRepository.from_settings() as repository:
        yield repository


def provide_gmail_subscriber_service(
    repository: Annotated[MailboxRepository, Depends(provide_mailbox_repository)],
    gmail: Annotated[Any, Depends(get_gmail_service)],
) -> GmailSubscriberService:
    return GmailSubscriberService(repository, gmail)
