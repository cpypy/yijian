# -*- mode: python ; coding: utf-8 -*-
"""sidecar 打包：单文件 exe，内置 tetonic 与 texlate。"""

import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_all

ROOT = Path.cwd()
if not (ROOT / "sidecar").exists():
    ROOT = Path(SPECPATH).resolve().parent  # noqa: F821

TECTONIC_NAME = "tectonic.exe" if os.name == "nt" else "tectonic"

datas = []
binaries = []
hiddenimports = []

for pkg in (
    "texlate",
    "typer",
    "rich",
    "httpx",
    "httpcore",
    "anyio",
    "yaml",
    "docx",
    "lxml",
    "bs4",
    "pydantic",
):
    try:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hiddenimports += h
    except Exception as exc:  # noqa: BLE001
        print(f"[spec] skip {pkg}: {exc}")

tectonic = ROOT / "data" / "texlate" / "tools" / TECTONIC_NAME
if tectonic.exists():
    datas.append((str(tectonic), "."))
else:
    print(f"[spec] 缺少 {tectonic}")

a = Analysis(  # noqa: F821
    [str(ROOT / "sidecar" / "runner.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["tkinter", "matplotlib", "streamlit", "IPython", "notebook", "PySide6"],
    noarchive=False,
)
pyz = PYZ(a.pure)  # noqa: F821

exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="yijian-engine",
    debug=False,
    strip=False,
    upx=False,
    console=True,
)

coll = COLLECT(  # noqa: F821
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="yijian-engine",
)
