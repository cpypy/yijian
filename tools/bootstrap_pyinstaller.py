"""把 PyInstaller 需要的 wheel 解压到 .build/pyi（不走 pip）。"""

from __future__ import annotations

import os
import zipfile
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
WHEELS = ROOT / ".wheels"
TARGET = ROOT / ".build" / "pyi"

PKGS = {
    "pyinstaller": "6.11.1",
    "pyinstaller-hooks-contrib": "2024.10",
    "altgraph": "0.17.4",
    "packaging": "24.2",
    "pefile": "2024.8.26",
    "pywin32-ctypes": "0.2.3",
    "setuptools": "75.6.0",
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
    return s


def main() -> int:
    WHEELS.mkdir(exist_ok=True)
    TARGET.mkdir(parents=True, exist_ok=True)
    with httpx.Client(proxy=PROXY, timeout=60, follow_redirects=True) as client:
        for name, ver in PKGS.items():
            meta = client.get(f"https://pypi.org/pypi/{name}/{ver}/json").json()
            cands = [u for u in meta["urls"] if u["filename"].endswith(".whl")]
            if not cands:
                print(f"[miss] {name}=={ver}")
                return 1
            cands.sort(key=lambda u: score(u["filename"]), reverse=True)
            wheel = cands[0]
            path = WHEELS / wheel["filename"]
            if not path.exists():
                path.write_bytes(client.get(wheel["url"]).content)
            with zipfile.ZipFile(path) as zf:
                zf.extractall(TARGET)
            print(f"[ok] {name}=={ver}")
    print(f"-> {TARGET}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
