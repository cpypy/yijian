"""译见引擎 sidecar。

被 Tauri 壳以子进程方式拉起，参数 `--run <arxiv_id>`，
在 stdout 上逐行输出 JSON 事件，由 Rust 侧转发给前端。

事件类型：
  {"event":"stage","stage":"fetch","progress":8}
  {"event":"log","line":"..."}
  {"event":"done","status":"clean","pdf":"<abs path>"}
  {"event":"error","message":"..."}
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path

FROZEN = bool(getattr(sys, "frozen", False))

if FROZEN:
    APP_DIR = Path(sys.executable).resolve().parent
    _home = os.environ.get("YIJIAN_HOME")
    BASE = Path(_home) if _home else Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "Yijian"
else:
    APP_DIR = Path(__file__).resolve().parents[1]
    BASE = APP_DIR

DATA = BASE / "data"
JOBS = DATA / "jobs"
TEXLATE_DATA = DATA / "texlate"
TEXLATE_CACHE = DATA / "texlate-cache"
TECTONIC_CACHE = DATA / "tectonic-cache"

STAGES = [
    ("fetch", 8, re.compile(r"arxiv|fetch|下载|获取|拉取", re.I)),
    ("parse", 22, re.compile(r"parse|segment|解析|分块|章节", re.I)),
    ("translate", 45, re.compile(r"translat|翻译|mock|占位", re.I)),
    ("compile", 78, re.compile(r"compil|tectonic|xelatex|编译|排版", re.I)),
    ("judge", 94, re.compile(r"judge|validate|判定|校验", re.I)),
]


def _patch_texlate() -> None:
    """运行时给 texlate 打两个兼容补丁（等价于 tools/patch_runtime.py）。

    放这里而不是改文件，是为了让 CI 从 PyPI 装的 texlate 也能直接跑。
    """
    # Windows 下 mkdir(mode=0o700) 会把目录 ACL 收紧，受限用户之后读不了
    if os.name == "nt":
        from pathlib import Path as _P

        if not getattr(_P.mkdir, "_yijian_patched", False):
            _orig = _P.mkdir

            def _mkdir(self, mode=0o777, parents=False, exist_ok=False):  # noqa: ANN001
                return _orig(self, parents=parents, exist_ok=exist_ok)

            _mkdir._yijian_patched = True  # type: ignore[attr-defined]
            _P.mkdir = _mkdir  # type: ignore[assignment]

    # 编译子进程 env 白名单：缺 SYSTEMROOT 会导致 Winsock 初始化失败
    from texlate.compile import sandbox

    sandbox._ENV_PASS_EXACT.update(  # noqa: SLF001
        {
            "TECTONIC_CACHE_DIR",
            "SYSTEMROOT",
            "SystemRoot",
            "WINDIR",
            "windir",
            "USERPROFILE",
            "APPDATA",
            "LOCALAPPDATA",
            "COMSPEC",
            "HTTP_PROXY",
            "HTTPS_PROXY",
            "ALL_PROXY",
            "NO_PROXY",
            "http_proxy",
            "https_proxy",
        }
    )


def emit(**payload) -> None:
    print(json.dumps(payload, ensure_ascii=False), flush=True)


def _prepare() -> None:
    for path in (TEXLATE_DATA, TEXLATE_CACHE, TECTONIC_CACHE, JOBS):
        path.mkdir(parents=True, exist_ok=True)

    target = TEXLATE_DATA / "tools" / "tectonic.exe"
    if not target.exists():
        for candidate in (APP_DIR / "tectonic.exe", APP_DIR / "tools" / "tectonic.exe"):
            if candidate.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(candidate, target)
                break


def _env() -> dict[str, str]:
    env = os.environ.copy()
    if not FROZEN:
        runtime = APP_DIR / ".runtime"
        if runtime.exists():
            env["PYTHONPATH"] = str(runtime)
    env["PYTHONIOENCODING"] = "utf-8"
    env["TEXLATE_DATA_DIR"] = str(TEXLATE_DATA)
    env["TEXLATE_CACHE_DIR"] = str(TEXLATE_CACHE)
    env["TECTONIC_CACHE_DIR"] = str(TECTONIC_CACHE)
    return env


def _tail(path: Path, limit: int = 20000) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")[-limit:]
    except FileNotFoundError:
        return ""


def _stage(text: str) -> tuple[str, float]:
    stage, pct = "fetch", 3.0
    for name, p, pat in STAGES:
        if pat.search(text):
            stage, pct = name, float(p)
    return stage, pct


def _find_pdf(root: Path) -> Path | None:
    pdfs = sorted(root.rglob("*.pdf"), key=lambda p: p.stat().st_size, reverse=True)
    return pdfs[0] if pdfs else None


def run(arxiv_id: str) -> int:
    _prepare()
    job_id = uuid.uuid4().hex[:12]
    work = JOBS / job_id
    build = work / "build"
    build.mkdir(parents=True, exist_ok=True)
    log_path = work / "run.log"

    if FROZEN:
        cmd = [sys.executable, "--texlate", "run", arxiv_id, "-w", str(build), "--keep"]
    else:
        cmd = [sys.executable, "-m", "texlate", "run", arxiv_id, "-w", str(build), "--keep"]

    emit(event="stage", stage="fetch", progress=2)
    try:
        with open(log_path, "w", encoding="utf-8", errors="replace") as fh:
            fh.write("$ " + " ".join(cmd) + "\n\n")
            fh.flush()
            proc = subprocess.Popen(  # noqa: S603
                cmd, stdout=fh, stderr=subprocess.STDOUT, env=_env(), cwd=str(work)
            )
            last = ("", 0.0)
            while proc.poll() is None:
                text = _tail(log_path)
                stage, pct = _stage(text)
                if (stage, pct) != last:
                    emit(event="stage", stage=stage, progress=pct)
                    last = (stage, pct)
                time.sleep(1.2)
            rc = proc.returncode
    except Exception as exc:  # noqa: BLE001
        emit(event="error", message=f"{type(exc).__name__}: {exc}")
        return 1

    text = _tail(log_path)
    pdf = _find_pdf(build)
    status = "failed"
    match = re.search(r'"status"\s*:\s*"(clean|partial|failed)"', text)
    if match:
        status = match.group(1)

    if pdf and status in {"clean", "partial"}:
        emit(event="done", status=status, pdf=str(pdf), job=job_id)
        return 0

    tail = "\n".join(text.strip().splitlines()[-8:])
    emit(event="error", message=f"exit={rc}\n{tail}", job=job_id)
    return 1


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "--texlate":
        _patch_texlate()
        from texlate.cli import app as cli_app

        sys.argv = ["texlate", *sys.argv[2:]]
        cli_app()
        return 0

    if len(sys.argv) >= 3 and sys.argv[1] == "--run":
        return run(sys.argv[2])

    emit(event="error", message="用法: yijian-engine --run <arxiv_id>")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
