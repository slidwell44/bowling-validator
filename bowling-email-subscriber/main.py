import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse

from config import ApplicationSettings
from gmail_subscriber.repositories import DatabaseUnavailable

logging.basicConfig(level=logging.INFO)

settings = ApplicationSettings()


def create_app() -> FastAPI:
    from gmail_subscriber.endpoints import router as gmail_router

    app = FastAPI(
        title=settings.NAME,
        version=settings.VERSION,
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

    @app.get("/", include_in_schema=False)
    async def redirect_root() -> RedirectResponse:
        return RedirectResponse("/docs")

    app.include_router(gmail_router)

    return app


app = create_app()
