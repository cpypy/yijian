"""不依赖 pip 的依赖安装器：从 PyPI 拉 wheel，解压到 .runtime。

pip 在本机沙箱里写临时文件会被拒（Permission denied），
但普通文件写入是允许的，而 wheel 本身就是个 zip，所以直接下载 + 解压。
"""

from __future__ import annotations

import os
import sys
import zipfile
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
WHEELS = ROOT / ".wheels"
RUNTIME = ROOT / ".runtime"

PKGS: dict[str, str] = {
    "annotated-doc": "0.0.5",
    "annotated-types": "0.8.0",
    "anyio": "4.16.0",
    "beautifulsoup4": "4.15.0",
    "certifi": "2026.10.23",
    "click": "8.5.0",
    "colorama": "0.4.6",
    "defusedxml": "0.7.1",
    "fastapi": "0.162.0",
    "h11": "0.16.0",
    "httpcore": "1.0.9",
    "httpx": "0.28.1",
    "idna": "3.20",
    "lxml": "6.1.3",
    "markdown-it-py": "4.2.0",
    "mdurl": "0.1.2",
    "opentelemetry-api": "1.45.2",
    "pydantic": "2.14.2",
    "pydantic-core": "2.50.2",
    "pygments": "2.21.0",
    "pypdf": "6.21.4",
    "python-docx": "1.2.0",
    "python-multipart": "0.0.33",
    "pyyaml": "6.0.3",
    "regex": "2026.9.29",
    "rich": "15.0.0",
    "shellingham": "1.5.4",
    "sniffio": "1.3.1",
    "soupsieve": "4.0",
    "sse-starlette": "3.6.0",
    "starlette": "1.10.0",
    "texlate": "0.1.1",
    "typer": "0.27.3",
    "typing-extensions": "4.16.0",
    "typing-inspection": "0.4.4",
    "uvicorn": "0.54.0",
}

PROXY = os.environ.get("HTTPS_PROXY") or os.environ.get("YIJIAN_PROXY") or None


def score(filename: str) -> int:
    n = filename.lower()
    if not n.endswith(".whl"):
        return -100
    if "musllinux" in n or "manylinux" in n or "macosx" in n:
        return -50
    s = 0
    if "py3-none-any" in n:
        s += 10
    if "cp312" in n:
        s += 30
    if "win_amd64" in n:
        s += 20
    if "abi3" in n:
        s += 2
    return s


def pick(urls: list[dict]) -> dict | None:
    cands = [u for u in urls if u["filename"].endswith(".whl")]
    cands.sort(key=lambda u: score(u["filename"]), reverse=True)
    return cands[0] if cands else None


def main() -> int:
    WHEELS.mkdir(exist_ok=True)
    RUNTIME.mkdir(exist_ok=True)
    ok = 0
    with httpx.Client(proxy=PROXY, timeout=60, follow_redirects=True) as client:
        for name, ver in PKGS.items():
            try:
                meta = client.get(f"https://pypi.org/pypi/{name}/{ver}/json").json()
            except Exception as exc:  # noqa: BLE001
                print(f"[skip] {name}=={ver}: {exc}")
                continue
            wheel = pick(meta.get("urls", []))
            if not wheel:
                print(f"[miss] {name}=={ver}: 没有可用 wheel")
                continue
            path = WHEELS / wheel["filename"]
            if not path.exists():
                path.write_bytes(client.get(wheel["url"]).content)
            with zipfile.ZipFile(path) as zf:
                zf.extractall(RUNTIME)
            ok += 1
            print(f"[ok] {name}=={ver} <- {wheel['filename']}")
    print(f"\n{ok}/{len(PKGS)} 个包就位 -> {RUNTIME}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
