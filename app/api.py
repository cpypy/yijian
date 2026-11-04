"""本地 HTTP API。只监听 127.0.0.1，端口随机。"""

from __future__ import annotations

import os
import re
import threading
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel

from . import db, engine

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"

app = FastAPI(title="译见")

ID_RE = re.compile(r"^(?:\d{4}\.\d{4,5}|[a-z\-]+(?:\.[A-Z]{2})?/\d{7})(?:v\d+)?$", re.I)


class ImportIn(BaseModel):
    arxiv_id: str


def _clean(raw: str) -> str:
    s = raw.strip()
    s = re.sub(r"^https?://(www\.)?arxiv\.org/(abs|pdf)/", "", s, flags=re.I)
    s = re.sub(r"\.pdf$", "", s, flags=re.I)
    return s.strip()


@app.get("/api/health")
def health() -> dict:
    return {"ok": True}


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return (WEB / "index.html").read_text(encoding="utf-8")


@app.post("/api/import")
def import_paper(body: ImportIn) -> dict:
    aid = _clean(body.arxiv_id)
    if not ID_RE.match(aid):
        raise HTTPException(status_code=400, detail="看起来不是 arXiv ID，例如 1706.03762")
    job_id = uuid.uuid4().hex[:12]
    now = int(time.time())
    db.execute(
        "INSERT INTO jobs (id, arxiv_id, state, stage, progress, created_at, updated_at)"
        " VALUES (?,?,?,?,?,?,?)",
        (job_id, aid, "queued", "queued", 0.0, now, now),
    )
    threading.Thread(target=engine.run_job, args=(job_id, aid), daemon=True).start()
    return {"id": job_id}


@app.get("/api/jobs")
def list_jobs() -> list[dict]:
    return db.query("SELECT * FROM jobs ORDER BY created_at DESC LIMIT 50")


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str) -> dict:
    job = db.one("SELECT * FROM jobs WHERE id = ?", (job_id,))
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在")
    return job


@app.get("/api/jobs/{job_id}/log")
def get_log(job_id: str, lines: int = 160) -> dict:
    return {"log": engine.log_text(job_id, lines)}


@app.get("/api/jobs/{job_id}/pdf")
def get_pdf(job_id: str) -> FileResponse:
    job = db.one("SELECT * FROM jobs WHERE id = ?", (job_id,))
    if not job or not job.get("pdf_path"):
        raise HTTPException(status_code=404, detail="还没有生成 PDF")
    path = Path(job["pdf_path"])
    if not path.exists():
        raise HTTPException(status_code=404, detail="PDF 文件不存在")
    return FileResponse(str(path), media_type="application/pdf", filename=f"{job['arxiv_id']}.pdf")


@app.post("/api/jobs/{job_id}/open")
def open_pdf(job_id: str) -> dict:
    job = db.one("SELECT * FROM jobs WHERE id = ?", (job_id,))
    if not job or not job.get("pdf_path"):
        raise HTTPException(status_code=404, detail="还没有生成 PDF")
    path = Path(job["pdf_path"])
    if not path.exists():
        raise HTTPException(status_code=404, detail="PDF 文件不存在")
    try:
        os.startfile(str(path))  # noqa: S606
    except AttributeError as exc:
        raise HTTPException(status_code=500, detail="当前系统不支持直接打开") from exc
    return {"ok": True}
