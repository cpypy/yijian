"""译见引擎 sidecar。

被 Tauri 壳以子进程方式拉起，参数 `--run <arxiv_id>`，
在 stdout 上逐行输出 JSON 事件，由 Rust 侧转发给前端。

内部流程：拉起 texlate server 子进程 → 提交翻译任务 → 轮询 →
落盘 segments.json（段落级 en/zh）→ 输出 done 事件。

事件类型：
  {"event":"stage","stage":"fetch","progress":8}
  {"event":"log","line":"..."}
  {"event":"done","status":"clean","pdf":"<abs>","segments":"<abs>","chunks":213}
  {"event":"error","message":"..."}
"""

from __future__ import annotations

import json
import os
import shutil
import socket
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

#: 安装包里预置的 TeX 宏包缓存（resources/texbundle）
BUNDLED_TEXBUNDLE = APP_DIR.parent / "texbundle"

TECTONIC_NAME = "tectonic.exe" if os.name == "nt" else "tectonic"

#: texlate 任务状态 → (阶段, 进度)
STATUS_STAGE = {
    "queued": ("fetch", 2),
    "fetching": ("fetch", 8),
    "parsing": ("parse", 22),
    "translating": ("translate", 45),
    "compiling": ("compile", 78),
    "judging": ("judge", 92),
    "done": ("done", 100),
    "partial": ("done", 100),
}

STAGE_ORDER = ["fetch", "parse", "translate", "compile", "judge", "done"]


def emit(**payload) -> None:
    print(json.dumps(payload, ensure_ascii=False), flush=True)


def _patch_texlate() -> None:
    """运行时给 texlate 打兼容补丁（等价于 tools/patch_runtime.py）。

    放这里而不是改文件，是为了让 CI 从 PyPI 装的 texlate 也能直接跑。
    """
    if os.name == "nt":
        from pathlib import Path as _P

        if not getattr(_P.mkdir, "_yijian_patched", False):
            _orig = _P.mkdir

            def _mkdir(self, mode=0o777, parents=False, exist_ok=False):  # noqa: ANN001
                return _orig(self, parents=parents, exist_ok=exist_ok)

            _mkdir._yijian_patched = True  # type: ignore[attr-defined]
            _P.mkdir = _mkdir  # type: ignore[assignment]

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


def _env() -> dict[str, str]:
    env = os.environ.copy()
    if not FROZEN:
        runtime = APP_DIR / ".runtime"
        if runtime.exists():
            env["PYTHONPATH"] = str(runtime)

    # 代理由用户自行配置（TUN 模式或系统级环境变量），这里不自动推断
    env["PYTHONIOENCODING"] = "utf-8"
    env["TEXLATE_DATA_DIR"] = str(TEXLATE_DATA)
    env["TEXLATE_CACHE_DIR"] = str(TEXLATE_CACHE)
    env["TECTONIC_CACHE_DIR"] = str(TECTONIC_CACHE)

    # 没配 key 时用 mock 占位译文，保证不配模型也能把链路跑通
    if not env.get("TEXLATE_API_KEY"):
        env.setdefault("TEXLATE_TRANSLATOR", "mock")
    return env


def _prepare() -> None:
    for path in (TEXLATE_DATA, TEXLATE_CACHE, TECTONIC_CACHE, JOBS):
        path.mkdir(parents=True, exist_ok=True)

    # 把安装包预置的宏包缓存合并到本地，缺什么补什么（半截缓存也能补齐）
    if BUNDLED_TEXBUNDLE.exists():
        copied = 0
        for item in BUNDLED_TEXBUNDLE.rglob("*"):
            if not item.is_file():
                continue
            target = TECTONIC_CACHE / item.relative_to(BUNDLED_TEXBUNDLE)
            if target.exists():
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, target)
            copied += 1
        if copied:
            emit(event="log", line=f"已载入预置 TeX 缓存：{copied} 个文件")

    target = TEXLATE_DATA / "tools" / TECTONIC_NAME
    if not target.exists():
        for candidate in (APP_DIR / TECTONIC_NAME, APP_DIR / "tools" / TECTONIC_NAME):
            if candidate.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(candidate, target)
                if os.name != "nt":
                    target.chmod(0o755)
                break


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _server_cmd(port: int) -> list[str]:
    args = ["--port", str(port), "--data-dir", str(TEXLATE_DATA)]
    if FROZEN:
        return [sys.executable, "--texlate-server", *args]
    return [sys.executable, "-m", "texlate.server", *args]


def _wait_server(client, timeout: float = 40.0) -> bool:
    import httpx

    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if client.get("/api/tasks", timeout=1.5).status_code == 200:
                return True
        except httpx.HTTPError:
            pass
        time.sleep(0.4)
    return False


def run(arxiv_id: str) -> int:
    import httpx

    _prepare()
    job_id = uuid.uuid4().hex[:12]
    work = JOBS / job_id
    work.mkdir(parents=True, exist_ok=True)
    log_path = work / "run.log"
    segments_path = work / "segments.json"

    port = _free_port()
    emit(event="stage", stage="fetch", progress=2)

    with open(log_path, "w", encoding="utf-8", errors="replace") as log_fh:
        server = subprocess.Popen(  # noqa: S603
            _server_cmd(port),
            stdout=log_fh,
            stderr=subprocess.STDOUT,
            env=_env(),
            cwd=str(work),
        )
        try:
            client = httpx.Client(
                base_url=f"http://127.0.0.1:{port}",
                timeout=30.0,
                trust_env=False,  # 本地回环不走代理
            )
            if not _wait_server(client):
                emit(event="error", message="引擎服务启动失败，详见 run.log")
                return 1

            resp = client.post(f"/api/arxiv/{arxiv_id}/translate", json={})
            if resp.status_code >= 400:
                emit(event="error", message=f"提交任务失败：{resp.status_code} {resp.text[:200]}")
                return 1
            task_id = resp.json()["task_id"]
            emit(event="log", line=f"任务 {task_id} 已创建")

            last_stage = ""
            last_log = 0.0
            task: dict = {}
            while True:
                time.sleep(1.0)
                task = client.get(f"/api/task/{task_id}").json()
                status = str(task.get("status") or "")
                stage, pct = STATUS_STAGE.get(status, ("fetch", 2))
                progress = task.get("progress")
                if isinstance(progress, (int, float)) and status not in {"done", "partial"}:
                    pct = min(95.0, float(progress))

                if stage != last_stage:
                    emit(event="stage", stage=stage, progress=pct)
                    last_stage = stage

                counters = task.get("counters") or {}
                if time.time() - last_log > 3:
                    last_log = time.time()
                    total = counters.get("total") or 0
                    done = counters.get("done") or 0
                    if total:
                        emit(event="log", line=f"翻译 {done}/{total}")

                if status in {"done", "partial"}:
                    break
                if status in {"failed", "cancelled", "needs_auth"}:
                    msg = task.get("message") or status
                    emit(event="error", message=f"任务失败：{msg}")
                    return 1

            # dual.json 里有每个占位符的原始 LaTeX（公式/引用），阅读器要靠它渲染
            dual = TEXLATE_DATA / "tasks" / task_id / "dual.json"
            chunks: list[dict] = []
            if dual.exists():
                try:
                    doc = json.loads(dual.read_text(encoding="utf-8"))
                    chunks = [
                        {
                            "seq": c.get("seq"),
                            "kind": c.get("kind") or "para",
                            "status": c.get("status") or "",
                            "en": c.get("en") or "",
                            "zh": c.get("zh") or "",
                            "ph": c.get("ph") or {},
                        }
                        for c in doc.get("chunks", [])
                    ]
                except json.JSONDecodeError:
                    chunks = []

            if not chunks:
                resp_chunks = client.get(f"/api/task/{task_id}/chunks")
                if resp_chunks.status_code == 200:
                    chunks = resp_chunks.json().get("chunks", [])

            segments_path.write_text(
                json.dumps({"chunks": chunks, "total": len(chunks)}, ensure_ascii=False),
                encoding="utf-8",
            )
            count = len(chunks)

            pdf = TEXLATE_DATA / "tasks" / task_id / "zh.pdf"
            emit(
                event="done",
                status=task.get("status", "done"),
                pdf=str(pdf) if pdf.exists() else "",
                segments=str(segments_path),
                chunks=count,
                job=job_id,
            )
            return 0
        except Exception as exc:  # noqa: BLE001
            emit(event="error", message=f"{type(exc).__name__}: {exc}")
            return 1
        finally:
            server.terminate()
            try:
                server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server.kill()


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "--texlate":
        _patch_texlate()
        from texlate.cli import app as cli_app

        sys.argv = ["texlate", *sys.argv[2:]]
        cli_app()
        return 0

    if len(sys.argv) > 1 and sys.argv[1] == "--texlate-server":
        _patch_texlate()
        from texlate.server.__main__ import main as server_main

        sys.argv = ["texlate.server", *sys.argv[2:]]
        server_main()
        return 0

    if len(sys.argv) >= 3 and sys.argv[1] == "--run":
        return run(sys.argv[2])

    emit(event="error", message="用法: yijian-engine --run <arxiv_id>")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
