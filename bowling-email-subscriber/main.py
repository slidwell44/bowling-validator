from fastapi import FastAPI
from fastapi.responses import RedirectResponse

from config import Settings, get_settings

settings: Settings = get_settings()


def create_app() -> FastAPI:
    from gmail_subscriber.endpoints import router as gmail_router

    app = FastAPI(
        title=settings.app.NAME,
        version=settings.app.VERSION,
    )

    @app.get("/hello")
    async def hello() -> dict[str, str]:
        return {"message": "Hello, World!"}

    @app.get("/", include_in_schema=False)
    async def redirect_root() -> RedirectResponse:
        return RedirectResponse("/docs")

    app.include_router(gmail_router)

    return app
