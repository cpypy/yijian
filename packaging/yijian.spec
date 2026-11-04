# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 配置：onedir + 内置 web 与 tectonic。"""

from pathlib import Path

from PyInstaller.utils.hooks import collect_all

ROOT = Path(SPECPATH).resolve().parent  # noqa: F821
CONSOLE = True  # 调试期留控制台；发布前改 False

datas = [(str(ROOT / "web"), "web")]
binaries = []
hiddenimports = []

for pkg in (
    "texlate",
    "uvicorn",
    "fastapi",
    "starlette",
    "pydantic",
    "typer",
    "rich",
    "httpx",
    "httpcore",
    "anyio",
    "sse_starlette",
    "yaml",
    "docx",
    "lxml",
    "bs4",
):
    try:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hiddenimports += h
    except Exception as exc:  # noqa: BLE001
        print(f"[spec] skip {pkg}: {exc}")

tectonic = ROOT / "data" / "texlate" / "tools" / "tectonic.exe"
if tectonic.exists():
    datas.append((str(tectonic), "tools"))
else:
    print(f"[spec] 缺少 {tectonic}，打包后无法编译")

a = Analysis(  # noqa: F821
    [str(ROOT / "packaging" / "entry.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "streamlit", "IPython", "notebook"],
    noarchive=False,
)
pyz = PYZ(a.pure)  # noqa: F821

exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Yijian",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=CONSOLE,
    disable_windowed_traceback=False,
)

coll = COLLECT(  # noqa: F821
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="Yijian",
)
