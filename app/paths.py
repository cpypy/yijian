"""运行期路径：开发态用仓库目录，打包后用 %LOCALAPPDATA%\\Yijian。"""

from __future__ import annotations

import os
import sys
from pathlib import Path

FROZEN = bool(getattr(sys, "frozen", False))

if FROZEN:
    APP_DIR = Path(sys.executable).resolve().parent
    RES_DIR = Path(getattr(sys, "_MEIPASS", APP_DIR))
    BASE = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "Yijian"
else:
    APP_DIR = Path(__file__).resolve().parents[1]
    RES_DIR = APP_DIR
    BASE = APP_DIR

WEB_DIR = RES_DIR / "web"
DATA_ROOT = BASE / "data"
JOBS_DIR = DATA_ROOT / "jobs"
TEXLATE_DATA = DATA_ROOT / "texlate"
TEXLATE_CACHE = DATA_ROOT / "texlate-cache"
TECTONIC_CACHE = DATA_ROOT / "tectonic-cache"
DB_PATH = DATA_ROOT / "app.db"
DEPS = APP_DIR / ".runtime"
