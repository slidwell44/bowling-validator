from common.services import BaseService
from config import Settings, get_settings

settings: Settings = get_settings()


class GmailSubscriberService(BaseService): ...
