from fastapi import FastAPI
from fastapi.responses import RedirectResponse

from config import ApplicationSettings

settings = ApplicationSettings()


def create_app() -> FastAPI:
    from gmail_subscriber.endpoints import router as gmail_router

    app = FastAPI(
        title=settings.NAME,
        version=settings.VERSION,
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
