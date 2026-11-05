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
import re
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

    # 把安装包预置的宏包缓存合并到本地。首次展开很慢（600+ 文件，
    # 还可能被杀软逐个扫描），所以用标记文件跳过后续重复扫描，并且
    # 全程打日志——否则界面会长时间停在 2% 看起来像卡死。
    marker = TECTONIC_CACHE / ".bundle-ok"
    stamp = str(int(BUNDLED_TEXBUNDLE.stat().st_mtime)) if BUNDLED_TEXBUNDLE.exists() else ""
    already = marker.exists() and marker.read_text(encoding="utf-8").strip() == stamp

    if BUNDLED_TEXBUNDLE.exists() and not already:
        files = [p for p in BUNDLED_TEXBUNDLE.rglob("*") if p.is_file()]
        emit(event="log", line=f"正在展开预置 TeX 缓存（{len(files)} 个文件，首次会慢一点）…")
        copied = 0
        for index, item in enumerate(files, 1):
            target = TECTONIC_CACHE / item.relative_to(BUNDLED_TEXBUNDLE)
            if not target.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(item, target)
                copied += 1
            if index % 150 == 0:
                emit(event="log", line=f"缓存 {index}/{len(files)}")
        marker.write_text(stamp, encoding="utf-8")
        emit(event="log", line=f"TeX 缓存就绪（新增 {copied} 个文件）")

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


def _fetch_meta(arxiv_id: str) -> dict[str, str]:
    """取标题/作者，供文库列表显示（一次请求，失败不影响主流程）。"""
    import xml.etree.ElementTree as ET

    import httpx

    ns = {"a": "http://www.w3.org/2005/Atom"}
    try:
        with httpx.Client(timeout=20.0, follow_redirects=True) as client:
            resp = client.get(
                "https://export.arxiv.org/api/query",
                params={"id_list": arxiv_id, "max_results": 1},
            )
        root = ET.fromstring(resp.text)
        entry = root.find("a:entry", ns)
        if entry is None:
            return {}
        title = " ".join((entry.findtext("a:title", "", ns) or "").split())
        authors = [
            (a.findtext("a:name", "", ns) or "").strip()
            for a in entry.findall("a:author", ns)
        ]
        published = (entry.findtext("a:published", "", ns) or "")[:10]
        return {
            "title": title,
            "authors": ", ".join([x for x in authors if x][:6]),
            "published": published,
        }
    except Exception:  # noqa: BLE001
        return {}


def _clean_tex(text: str) -> str:
    """粗暴清洗 bibitem 文本：去掉常见命令，保留可读内容。"""
    text = re.sub(r"\\emph\{([^}]*)\}", r"\1", text)
    text = re.sub(r"\\textit\{([^}]*)\}", r"\1", text)
    text = re.sub(r"\\textbf\{([^}]*)\}", r"\1", text)
    text = re.sub(r"\\url\{([^}]*)\}", r"\1", text)
    text = re.sub(r"\\[a-zA-Z]+\*?", "", text)
    text = text.replace("{", "").replace("}", "").replace("~", " ")
    return re.sub(r"\s+", " ", text).strip()


def _parse_bbl(task_dir: Path) -> dict[str, dict[str, str]]:
    """从编译产物的 .bbl 里抽参考文献，离线、不联网。"""
    candidates = sorted((task_dir / "base").glob("*.bbl"))
    if not candidates:
        candidates = sorted(task_dir.glob("*.bbl"))
    if not candidates:
        return {}

    text = max(candidates, key=lambda p: p.stat().st_size).read_text(
        encoding="utf-8", errors="replace"
    )
    text = text.split("\\end{thebibliography}")[0]

    parts = re.split(r"\\bibitem(?:\[[^\]]*\])?\{([^}]+)\}", text)
    refs: dict[str, dict[str, str]] = {}
    for i in range(1, len(parts) - 1, 2):
        key = parts[i].strip()
        body = parts[i + 1]
        chunks = [c.strip() for c in re.split(r"\\newblock", body) if c.strip()]
        refs[key] = {
            "authors": _clean_tex(chunks[0]) if chunks else "",
            "title": _clean_tex(chunks[1]) if len(chunks) > 1 else "",
            "venue": _clean_tex(chunks[2]) if len(chunks) > 2 else "",
        }
    return refs


def _sync_glossary() -> None:
    """把应用里的术语表写成 texlate 认识的 glossary.yaml（优先级最高的用户表）。"""
    src = BASE / "glossary.json"
    if not src.exists():
        return
    try:
        items = json.loads(src.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    if not isinstance(items, list):
        return

    lines = []
    for item in items:
        if not isinstance(item, dict):
            continue
        en = str(item.get("en") or "").strip()
        zh = str(item.get("zh") or "").strip()
        if not en or not zh:
            continue
        # JSON 字符串同时是合法的 YAML 标量
        lines.append(f"{json.dumps(en, ensure_ascii=False)}: {json.dumps(zh, ensure_ascii=False)}")

    target = TEXLATE_DATA / "glossary.yaml"
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    if lines:
        emit(event="log", line=f"已应用术语表：{len(lines)} 条")


def _arxiv_base(value: str) -> str:
    """任务表里 arxiv_id 带版本号（1706.03762v7），比较前统一去掉。"""
    return re.sub(r"v\d+$", "", str(value or "").strip())


def _find_existing_task(client, arxiv_id: str) -> tuple[str, str] | None:
    """按 arXiv ID 找未完成任务（409 的兜底路径）。"""
    try:
        tasks = client.get("/api/tasks").json().get("tasks", [])
    except Exception:  # noqa: BLE001
        return None

    base = _arxiv_base(arxiv_id)
    candidates = [
        t
        for t in tasks
        if _arxiv_base(t.get("arxiv_id")) == base
        and t.get("status") not in {"done", "partial"}
    ]
    if not candidates:
        return None
    candidates.sort(key=lambda t: t.get("updated_at") or 0, reverse=True)
    top = candidates[0]
    return str(top["task_id"]), str(top.get("status") or "")


RETRYABLE = {"interrupted", "fault", "partial", "cancelled", "needs_auth"}
ACTIVE = {"queued", "fetching", "parsing", "translating", "compiling", "judging"}


def _list_tasks(client) -> list[dict]:
    try:
        return client.get("/api/tasks").json().get("tasks", [])
    except Exception:  # noqa: BLE001
        return []


def _pick_task(tasks: list[dict], arxiv_id: str) -> dict | None:
    """只挑「没跑完」的任务：已完成的不该打扰用户。"""
    base = _arxiv_base(arxiv_id)
    cands = [
        t
        for t in tasks
        if _arxiv_base(t.get("arxiv_id")) == base
        and t.get("status") not in {"done", "partial"}
    ]
    if not cands:
        return None
    cands.sort(key=lambda t: t.get("updated_at") or 0, reverse=True)
    return cands[0]


def _read_new(path: Path, offset: int, limit: int = 16000) -> tuple[str, int]:
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            fh.seek(offset)
            chunk = fh.read(limit)
            return chunk, fh.tell()
    except FileNotFoundError:
        return "", offset


def run(arxiv_id: str, mode: str = "auto") -> int:
    """mode: auto（有旧任务就报冲突）/ resume（续跑）/ fresh（删掉重来）"""
    import httpx

    emit(event="log", line="准备运行环境…")
    _prepare()
    _sync_glossary()
    started = time.time()

    job_id = uuid.uuid4().hex[:12]
    work = JOBS / job_id
    work.mkdir(parents=True, exist_ok=True)
    log_path = work / "run.log"
    segments_path = work / "segments.json"

    port = _free_port()
    emit(event="stage", stage="fetch", progress=2)
    emit(event="log", line="启动引擎服务…")

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
                trust_env=False,
            )
            if not _wait_server(client):
                emit(event="error", message="引擎服务 40 秒内没起来，详见 run.log")
                return 1
            emit(event="log", line="引擎就绪")

            existing = _pick_task(_list_tasks(client), arxiv_id)
            task_id: str | None = None

            # 已有任务，且用户还没决定：交给界面问「继续 / 重译」
            if existing and mode == "auto":
                emit(
                    event="conflict",
                    task_id=str(existing.get("task_id") or ""),
                    status=str(existing.get("status") or ""),
                    progress=existing.get("progress") or 0,
                )
                return 0

            if existing and mode == "resume":
                task_id = str(existing["task_id"])
                status = str(existing.get("status") or "")
                if status in RETRYABLE:
                    resp = client.post(f"/api/task/{task_id}/retry", json={})
                    if resp.status_code >= 400:
                        emit(
                            event="error",
                            message=f"续跑失败：{resp.status_code} {resp.text[:160]}",
                        )
                        return 1
                    emit(event="log", line=f"继续上次任务（{status}）")
                else:
                    emit(event="log", line=f"附着到正在运行的任务（{status}）")

            elif existing and mode == "fresh":
                task_id = str(existing["task_id"])
                status = str(existing.get("status") or "")
                if status in ACTIVE:
                    client.post(f"/api/task/{task_id}/cancel")
                    time.sleep(1.5)
                client.delete(f"/api/task/{task_id}")
                emit(event="log", line="已删除旧任务，重新开始")
                task_id = None

            if task_id is None:
                resp = client.post(f"/api/arxiv/{arxiv_id}/translate", json={})
                if resp.status_code == 409:
                    # 服务端说已有活跃任务，响应体里带 task_id
                    try:
                        detail = resp.json()
                    except Exception:  # noqa: BLE001
                        detail = {}
                    dup = str(detail.get("task_id") or "")

                    if not dup:
                        emit(event="error", message=f"提交被拒（409）：{resp.text[:200]}")
                        return 1

                    if mode == "auto":
                        emit(
                            event="conflict",
                            task_id=dup,
                            status=str(detail.get("code") or "active"),
                            progress=0,
                        )
                        return 0

                    if mode == "resume":
                        emit(event="log", line=f"附着到已有任务 {dup}")
                        task_id = dup
                    else:  # fresh
                        client.post(f"/api/task/{dup}/cancel")
                        time.sleep(1.5)
                        client.delete(f"/api/task/{dup}")
                        retry = client.post(f"/api/arxiv/{arxiv_id}/translate", json={})
                        if retry.status_code >= 400:
                            emit(
                                event="error",
                                message=f"重建任务失败：{retry.status_code} {retry.text[:160]}",
                            )
                            return 1
                        task_id = retry.json()["task_id"]
                        emit(event="log", line=f"已删除旧任务，重建 {task_id}")
                elif resp.status_code >= 400:
                    emit(
                        event="error",
                        message=f"提交任务失败：{resp.status_code} {resp.text[:200]}",
                    )
                    return 1
                else:
                    task_id = resp.json()["task_id"]
                    emit(event="log", line=f"任务 {task_id} 已创建")

            last_stage = ""
            last_done = -1
            log_offset = 0
            model_failures = 0
            stall_ticks = 0

            while True:
                time.sleep(1.0)
                task = client.get(f"/api/task/{task_id}").json()
                status = str(task.get("status") or "")
                stage, pct = STATUS_STAGE.get(status, ("fetch", 2))

                progress_value = task.get("progress")
                if isinstance(progress_value, (int, float)) and status not in {"done", "partial"}:
                    pct = min(95.0, float(progress_value))

                if stage != last_stage:
                    emit(event="stage", stage=stage, progress=pct)
                    last_stage = stage

                counters = task.get("counters") or {}
                done = int(counters.get("done") or 0)
                total = int(counters.get("total") or 0)
                if total and done != last_done:
                    last_done = done
                    stall_ticks = 0
                    emit(event="log", line=f"翻译 {done}/{total}")
                elif status == "translating":
                    stall_ticks += 1

                # 把引擎侧 warning/error 透到界面，502/401 累计到阈值就快退
                chunk, log_offset = _read_new(log_path, log_offset)
                for line in chunk.splitlines():
                    low = line.lower()
                    if "warning" in low or "error" in low or "失败" in line:
                        text_line = line.strip()[:300]
                        emit(event="log", line=text_line)
                        if "502" in text_line or "401" in text_line:
                            model_failures += 1

                if model_failures >= 6:
                    emit(
                        event="error",
                        message="模型端点连续失败（502/401）：请检查 Base URL、模型名与 API Key",
                    )
                    return 1
                if stall_ticks > 300:
                    emit(event="error", message="翻译超过 5 分钟无进展，已中止")
                    return 1

                if status in {"done", "partial"}:
                    break
                if status in {"failed", "cancelled", "needs_auth"}:
                    message = task.get("message") or status
                    emit(event="error", message=f"任务失败：{message}")
                    return 1

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
                json.dumps(
                    {
                        "chunks": chunks,
                        "total": len(chunks),
                        "refs": _parse_bbl(TEXLATE_DATA / "tasks" / task_id),
                        "meta": _fetch_meta(arxiv_id),
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            pdf = TEXLATE_DATA / "tasks" / task_id / "zh.pdf"
            counters = task.get("counters") or {}
            emit(
                event="done",
                status=task.get("status", "done"),
                pdf=str(pdf) if pdf.exists() else "",
                segments=str(segments_path),
                chunks=len(chunks),
                tokens=counters.get("tokens") or 0,
                seconds=round(time.time() - started, 1),
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


def discover() -> int:
    """拉 alphaXiv 全站热榜（近 7 天），输出 JSON 给桌面端。"""
    import httpx

    url = "https://api.alphaxiv.org/papers/v3/feed"
    params = {"pageNum": 0, "pageSize": 100, "sort": "Hot", "interval": "7 Days"}
    try:
        with httpx.Client(timeout=25.0, follow_redirects=True) as client:
            resp = client.get(url, params=params)
            resp.raise_for_status()
            papers = resp.json().get("papers", [])
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False))
        return 1

    items = []
    seen: set[str] = set()
    arxiv_re = re.compile(r"^\d{4}\.\d{4,5}$")
    for p in papers:
        pid = str(p.get("universal_paper_id") or p.get("id") or "")
        if not pid or pid in seen:
            continue
        # alphaXiv 的部分条目是 slug 而非 arXiv ID，译不了，直接跳过
        if not arxiv_re.match(pid):
            continue
        seen.add(pid)

        authors = p.get("authors") or []
        if authors and isinstance(authors[0], dict):
            names = [str(a.get("name") or "") for a in authors[:6]]
        elif isinstance(authors, list):
            names = [str(a) for a in authors[:6]]
        else:
            names = []

        metrics = p.get("metrics") or {}
        visits = metrics.get("visits_count") or {}
        items.append(
            {
                "id": pid,
                "arxiv_id": str(p.get("universal_paper_id") or ""),
                "title": str(p.get("title") or "").strip(),
                "authors": ", ".join(n for n in names if n),
                "abstract": str(p.get("abstract") or "").strip(),
                "votes": metrics.get("total_votes"),
                "visits": visits.get("last_7_days"),
                "topics": [t for t in (p.get("topics") or []) if isinstance(t, str)][:3],
            }
        )

    print(json.dumps({"items": items}, ensure_ascii=False))
    return 0


def main() -> int:
    # Windows 上 stdout 默认是 GBK，热榜里的 ö 会直接抛 UnicodeEncodeError
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    if len(sys.argv) > 1 and sys.argv[1] == "--discover":
        _patch_texlate()
        return discover()

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
        mode = "auto"
        if "--mode" in sys.argv:
            idx = sys.argv.index("--mode")
            if idx + 1 < len(sys.argv):
                mode = sys.argv[idx + 1]
        return run(sys.argv[2], mode)

    emit(event="error", message="用法: yijian-engine --run <arxiv_id>")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
