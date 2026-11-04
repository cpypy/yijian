"""把 PySide6（Qt）解压到 .runtime（不走 pip）。"""

from __future__ import annotations

import os
import zipfile
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
WHEELS = ROOT / ".wheels"
TARGET = ROOT / ".runtime"

PKGS = {
    "PySide6-Essentials": "6.8.1",
    "shiboken6": "6.8.1",
}

PROXY = os.environ.get("HTTPS_PROXY") or os.environ.get("YIJIAN_PROXY") or None


def score(filename: str) -> int:
    n = filename.lower()
    if not n.endswith(".whl"):
        return -100
    if "musllinux" in n or "manylinux" in n or "macosx" in n:
        return -50
    s = 0
    if "win_amd64" in n:
        s += 20
    if "cp39-abi3" in n or "abi3" in n:
        s += 15
    if "cp312" in n:
        s += 20
    return s


def main() -> int:
    WHEELS.mkdir(exist_ok=True)
    TARGET.mkdir(exist_ok=True)
    with httpx.Client(proxy=PROXY, timeout=300, follow_redirects=True) as client:
        for name, ver in PKGS.items():
            meta = client.get(f"https://pypi.org/pypi/{name}/{ver}/json").json()
            cands = [u for u in meta["urls"] if u["filename"].endswith(".whl")]
            cands.sort(key=lambda u: score(u["filename"]), reverse=True)
            if not cands:
                print(f"[miss] {name}=={ver}")
                return 1
            wheel = cands[0]
            path = WHEELS / wheel["filename"]
            if not path.exists():
                print(f"[dl] {wheel['filename']} ({wheel['size'] / 1e6:.0f} MB)")
                path.write_bytes(client.get(wheel["url"]).content)
            with zipfile.ZipFile(path) as zf:
                zf.extractall(TARGET)
            print(f"[ok] {name}=={ver}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
