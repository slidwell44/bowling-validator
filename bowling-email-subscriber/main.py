from fastapi import FastAPI
from fastapi.responses import RedirectResponse

from config import get_settings

settings = get_settings()


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.app.NAME,
        version=settings.app.VERSION,
    )

    @app.get("/hello")
    async def hello():
        return {"message": "Hello, World!"}

    @app.get("/", include_in_schema=False)
    async def redirect_root():
        return RedirectResponse("/docs")

    return app
