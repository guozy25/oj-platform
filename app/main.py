from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.router import api_router
from app.core.config import Settings
from app.core.errors import install_exception_handlers
from app.db.database import Database


def create_app(settings: Settings | None = None) -> FastAPI:
    app_settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        database = Database(app_settings)
        await database.initialize()
        application.state.settings = app_settings
        application.state.database = database
        application.state.background_tasks = set()
        try:
            yield
        finally:
            tasks = list(application.state.background_tasks)
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)

    logging.basicConfig(
        level=getattr(logging, app_settings.log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    application = FastAPI(
        title="Async OJ",
        description="Programming and Training (Python) Online Judge",
        version="0.1.0",
        lifespan=lifespan,
    )
    install_exception_handlers(application)
    application.include_router(api_router)
    return application


app = create_app()
