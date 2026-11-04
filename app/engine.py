"""texlate 任务适配层。

W1 只做一件事：调 `python -m texlate run <arxiv_id>` 跑 mock 管线，
验证「取源 → 分段 → 占位翻译 → ctex 编译」这条链路在本机是通的。
真翻译（BYOK）在 W3 接。
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

from . import db
from .paths import (
    DEPS,
    FROZEN,
    JOBS_DIR,
    TECTONIC_CACHE,
    TEXLATE_CACHE,
    TEXLATE_DATA,
)

# 日志里出现这些词，就认为进入了对应阶段
STAGES = [
    ("fetch", 8, re.compile(r"arxiv|fetch|下载|获取|拉取", re.I)),
    ("parse", 22, re.compile(r"parse|segment|解析|分块|章节", re.I)),
    ("translate", 45, re.compile(r"translat|翻译|mock|占位", re.I)),
    ("compile", 78, re.compile(r"compil|tectonic|xelatex|编译|排版", re.I)),
    ("judge", 94, re.compile(r"judge|validate|判定|校验", re.I)),
]

_lock = threading.Lock()


def _env() -> dict[str, str]:
    env = os.environ.copy()
    # 代理只从环境读：HTTPS_PROXY 优先，其次项目自定义的 YIJIAN_PROXY。
    proxy = env.get("HTTPS_PROXY") or env.get("YIJIAN_PROXY")
    if proxy and not env.get("HTTPS_PROXY"):
        env["HTTPS_PROXY"] = proxy
        env["HTTP_PROXY"] = proxy
    env["PYTHONIOENCODING"] = "utf-8"
    if not FROZEN and DEPS.exists():
        env["PYTHONPATH"] = str(DEPS) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    # texlate 的运行时目录全部落在数据目录，避免写用户目录
    env["TEXLATE_DATA_DIR"] = str(TEXLATE_DATA)
    env["TEXLATE_CACHE_DIR"] = str(TEXLATE_CACHE)
    # tectonic 默认把 bundle 缓存写到 %LOCALAPPDATA%，受限环境会直接失败
    env["TECTONIC_CACHE_DIR"] = str(TECTONIC_CACHE)
    for path in (TEXLATE_DATA, TEXLATE_CACHE, TECTONIC_CACHE):
        path.mkdir(parents=True, exist_ok=True)
    return env


def _tail(path: Path, limit: int = 12000) -> str:
    try:
        data = path.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return ""
    return data[-limit:]


def _detect_stage(text: str) -> tuple[str, float]:
    stage, pct = "fetch", 3.0
    for name, p, pat in STAGES:
        if pat.search(text):
            stage, pct = name, float(p)
    return stage, pct


def _set(job_id: str, **fields) -> None:
    fields["updated_at"] = int(time.time())
    cols = ", ".join(f"{k} = ?" for k in fields)
    db.execute(f"UPDATE jobs SET {cols} WHERE id = ?", (*fields.values(), job_id))


def _find_pdf(workdir: Path) -> Path | None:
    pdfs = sorted(workdir.rglob("*.pdf"), key=lambda p: p.stat().st_size, reverse=True)
    if not pdfs:
        return None
    for p in pdfs:
        if p.stem.lower().endswith("zh") or "_zh" in p.stem.lower():
            return p
    return pdfs[0]


def run_job(job_id: str, arxiv_id: str) -> None:
    workdir = JOBS_DIR / job_id
    workdir.mkdir(parents=True, exist_ok=True)
    build = workdir / "build"
    build.mkdir(exist_ok=True)
    log_path = workdir / "run.log"
    if FROZEN:
        # 打包后 sys.executable 是应用自己，用 --texlate 子命令转发给引擎
        cmd = [sys.executable, "--texlate", "run", arxiv_id, "-w", str(build), "--keep"]
    else:
        cmd = [sys.executable, "-m", "texlate", "run", arxiv_id, "-w", str(build), "--keep"]

    _set(job_id, state="running", stage="fetch", progress=2.0, workdir=str(workdir))

    try:
        with open(log_path, "w", encoding="utf-8", errors="replace") as fh:
            fh.write("$ " + " ".join(cmd) + "\n\n")
            fh.flush()
            # 注意：这里必须把 stdout 重定向到文件而不是 PIPE。
            # 沙箱环境里子进程的管道 stdio 会被拒绝，文件重定向是稳的。
            proc = subprocess.Popen(  # noqa: S603
                cmd,
                cwd=str(workdir),
                stdout=fh,
                stderr=subprocess.STDOUT,
                env=_env(),
            )
            while proc.poll() is None:
                text = _tail(log_path)
                stage, pct = _detect_stage(text)
                _set(job_id, stage=stage, progress=pct)
                time.sleep(1.5)
            rc = proc.returncode
    except Exception as exc:  # noqa: BLE001
        _set(job_id, state="failed", error=f"{type(exc).__name__}: {exc}")
        return

    text = _tail(log_path, 20000)
    pdf = _find_pdf(build)

    if rc == 0 and pdf:
        _set(job_id, state="done", stage="done", progress=100.0, pdf_path=str(pdf), error=None)
        return

    status = "failed"
    m = re.search(r'"status"\s*:\s*"(clean|partial|failed)"', text)
    if m:
        status = m.group(1)
    if status in {"clean", "partial"} and pdf:
        _set(job_id, state="done", stage="done", progress=100.0, pdf_path=str(pdf))
        return

    last = "\n".join(text.strip().splitlines()[-6:])
    _set(job_id, state="failed", error=f"exit={rc}\n{last}")


def log_text(job_id: str, lines: int = 120) -> str:
    path = JOBS_DIR / job_id / "run.log"
    if not path.exists():
        return ""
    text = path.read_text(encoding="utf-8", errors="replace")
    return "\n".join(text.splitlines()[-lines:])
