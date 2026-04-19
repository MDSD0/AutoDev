from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

app = FastAPI(title="AutoDev Hosted Shell")

FRONTEND_DIR = os.path.join(os.path.dirname(__file__), "frontend")
app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(os.path.join(FRONTEND_DIR, "index.html"))


@app.get("/settings")
def settings() -> FileResponse:
    return FileResponse(os.path.join(FRONTEND_DIR, "settings.html"))


@app.get("/favicon.ico")
def favicon() -> FileResponse:
    return FileResponse(
        os.path.join(FRONTEND_DIR, "favicon.svg"),
        media_type="image/svg+xml",
    )


@app.get("/status")
def status() -> dict[str, str]:
    return {
        "status": "running",
        "mode": "hosted-shell",
    }
