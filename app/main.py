"""桌面入口：起本地 API，再开一个原生窗口。

窗口优先级：pywebview（若可用）→ Edge/Chrome 的 --app 模式（无地址栏）。
"""

from __future__ import annotations

import argparse
import os
import shutil
import socket
import subprocess
import threading
import time
import webbrowser
from pathlib import Path

import uvicorn

from .api import app
from .paths import BASE, RES_DIR, TEXLATE_DATA


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _wait_ready(url: str, timeout: float = 20.0) -> bool:
    import httpx

    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if httpx.get(url + "/api/health", timeout=0.6).status_code == 200:
                return True
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.2)
    return False


def _prepare_tectonic() -> None:
    """把安装包里自带的 tectonic 复制到数据目录，texlate 会优先用托管件。"""
    target = TEXLATE_DATA / "tools" / "tectonic.exe"
    if target.exists():
        return
    bundled = RES_DIR / "tools" / "tectonic.exe"
    if bundled.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(bundled, target)


def _find_browser() -> str | None:
    candidates = [
        Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"))
        / "Microsoft/Edge/Application/msedge.exe",
        Path(os.environ.get("PROGRAMFILES", r"C:\Program Files"))
        / "Microsoft/Edge/Application/msedge.exe",
        Path(os.environ.get("PROGRAMFILES", r"C:\Program Files"))
        / "Google/Chrome/Application/chrome.exe",
    ]
    for path in candidates:
        if path.exists():
            return str(path)
    return None


def _find_window(title: str) -> list[int]:
    """列出标题包含 title 的可见顶层窗口（ctypes，无需第三方依赖）。"""
    import ctypes

    user32 = ctypes.windll.user32
    found: list[int] = []
    proc_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    def callback(hwnd, _lparam):  # noqa: ANN001
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        if title in buf.value:
            found.append(int(hwnd))
        return True

    user32.EnumWindows(proc_type(callback), 0)
    return found


def _wait_window_lifecycle(title: str, appear_timeout: float = 45.0) -> None:
    """等窗口出现，再等它消失，然后退出进程。"""
    deadline = time.time() + appear_timeout
    while time.time() < deadline:
        if _find_window(title):
            break
        time.sleep(1.0)
    while _find_window(title):
        time.sleep(1.5)


def _open_app_window(url: str) -> None:
    """用 Chromium 的 --app 模式开一个无地址栏窗口，独立 profile 避免复用日常浏览器。"""
    profile = BASE / "window-profile"
    profile.mkdir(parents=True, exist_ok=True)

    try:
        import webview  # type: ignore[import-not-found]

        webview.create_window("译见", url, width=1180, height=800, min_size=(920, 600))
        webview.start()
        return
    except Exception:  # noqa: BLE001
        pass

    browser = _find_browser()
    if not browser:
        webbrowser.open(url)
        return

    subprocess.Popen(  # noqa: S603
        [
            browser,
            f"--app={url}",
            f"--user-data-dir={profile}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-background-mode",
            "--window-size=1180,800",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def main() -> None:
    ap = argparse.ArgumentParser(description="译见 · 桌面入口")
    ap.add_argument("--headless", action="store_true", help="只起 API，不开窗口")
    ap.add_argument("--port", type=int, default=0)
    args = ap.parse_args()

    _prepare_tectonic()

    port = args.port or _free_port()
    url = f"http://127.0.0.1:{port}"

    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()

    if not _wait_ready(url):
        print("API 启动失败")
        return
    print(f"API: {url}")

    if args.headless:
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            pass
        return

    _open_app_window(url)
    _wait_window_lifecycle("译见")
    server.should_exit = True


if __name__ == "__main__":
    main()
