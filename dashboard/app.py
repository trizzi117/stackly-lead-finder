"""Дашборд Stackly Lead Finder — FastAPI.

Запуск:  uvicorn dashboard.app:app --reload
Открыть: http://127.0.0.1:8000
"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from src.settings import settings
from src.storage import Storage

STATIC = Path(__file__).parent / "static"

app = FastAPI(title="Stackly Lead Finder", version="0.1.0")
storage = Storage(settings.db_abspath)


class StatusBody(BaseModel):
    status: str


class NoteBody(BaseModel):
    note: str


VALID_STATUS = {"new", "contacted", "converted", "dismissed"}


@app.get("/api/stats")
def stats() -> dict:
    s = storage.stats()
    s["niche"] = settings.niche_title
    return s


@app.get("/api/leads")
def leads(classification: str = "all", status: str = "all", limit: int = 200) -> list[dict]:
    return storage.list_leads(classification=classification, status=status, limit=limit)


@app.post("/api/leads/{lead_id}/status")
def set_status(lead_id: int, body: StatusBody) -> dict:
    if body.status not in VALID_STATUS:
        raise HTTPException(400, f"status должен быть из {VALID_STATUS}")
    if not storage.get_lead(lead_id):
        raise HTTPException(404, "лид не найден")
    storage.update_status(lead_id, body.status)
    return {"ok": True}


@app.post("/api/leads/{lead_id}/note")
def set_note(lead_id: int, body: NoteBody) -> dict:
    if not storage.get_lead(lead_id):
        raise HTTPException(404, "лид не найден")
    storage.update_note(lead_id, body.note[:2000])
    return {"ok": True}


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


# статика (если понадобится расширять)
if STATIC.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")
