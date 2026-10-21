import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import logfire
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from config import ApplicationSettings, LogfireSettings
from gmail_subscriber.repositories import DatabaseUnavailable
from pages import router as pages_router

logging.basicConfig(level=logging.INFO)

settings = ApplicationSettings()
logfire_settings = LogfireSettings()

logfire.configure(
    service_name=settings.NAME,
    service_version=settings.VERSION,
    token=(
        logfire_settings.TOKEN.get_secret_value() if logfire_settings.TOKEN else None
    ),
    send_to_logfire="if-token-present",
)
logfire.instrument_system_metrics()


def create_app() -> FastAPI:
    from gmail_subscriber.endpoints import router as gmail_router
    from gmail_subscriber.repositories import MailboxRepository

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # Create the schema once at startup instead of on every request.
        with MailboxRepository.from_settings():
            pass
        yield

    app = FastAPI(
        title=settings.NAME,
        version=settings.VERSION,
        lifespan=lifespan,
    )
    logfire.instrument_fastapi(app)

    from gmail_subscriber.services import GmailAuthorizationExpired

    @app.exception_handler(GmailAuthorizationExpired)
    async def gmail_authorization_expired_handler(
        request: Request, exc: GmailAuthorizationExpired
    ) -> JSONResponse:
        logging.getLogger(__name__).error(
            "Gmail authorization expired for %s: %s", request.url.path, exc
        )
        return JSONResponse(
            status_code=503,
            content={"detail": "Gmail authorization expired"},
            headers={"Retry-After": "300"},
        )

    @app.exception_handler(DatabaseUnavailable)
    async def database_unavailable_handler(
        request: Request, exc: DatabaseUnavailable
    ) -> JSONResponse:
        logging.getLogger(__name__).exception(
            "Database unavailable for %s: %s", request.url.path, exc
        )
        return JSONResponse(
            status_code=503,
            content={"detail": "Database temporarily unavailable"},
            headers={"Retry-After": "30"},
        )

    @app.get("/hello")
    async def hello() -> dict[str, str]:
        return {"message": "Hello, World!"}

    app.include_router(pages_router)
    app.include_router(gmail_router)

    return app


app = create_app()
