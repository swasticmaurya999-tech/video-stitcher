"""FastAPI application: wires routes, the background worker, error handlers, and the demo page."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from app import worker
from app.api.routes import router
from app.errors import AppError, app_error_handler, unhandled_error_handler

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s :: %(message)s")

WEB_DIR = Path(__file__).parent / "web"


@asynccontextmanager
async def lifespan(_: FastAPI):
    worker.start()
    try:
        yield
    finally:
        worker.stop()


app = FastAPI(title="Video Stitcher", version="1.0.0", lifespan=lifespan)
app.add_exception_handler(AppError, app_error_handler)
app.add_exception_handler(Exception, unhandled_error_handler)
app.include_router(router)


@app.get("/", response_class=HTMLResponse)
async def index() -> str:
    page = WEB_DIR / "index.html"
    if page.exists():
        return page.read_text(encoding="utf-8")
    return "<h1>Video Stitcher</h1><p>API is running. See /docs.</p>"
