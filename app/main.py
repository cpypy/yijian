"""桌面入口：起本地 API，再开一个原生窗口（WebView2）。"""

from __future__ import annotations

import argparse
import socket
import threading
import time
import webbrowser

import uvicorn

from .api import app


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _wait_ready(url: str, timeout: float = 15.0) -> bool:
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


def main() -> None:
    ap = argparse.ArgumentParser(description="译见 · 桌面入口")
    ap.add_argument("--headless", action="store_true", help="只起 API，不开窗口")
    ap.add_argument("--port", type=int, default=0)
    args = ap.parse_args()

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

    try:
        import webview
    except Exception as exc:  # noqa: BLE001
        print(f"pywebview 不可用（{exc}），改用浏览器打开")
        webbrowser.open(url)
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            pass
        return

    webview.create_window("译见", url, width=1180, height=800, min_size=(920, 600))
    webview.start()
    server.should_exit = True


if __name__ == "__main__":
    main()
