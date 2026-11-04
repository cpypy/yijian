"""把项目内的 .deps 加到 sys.path，避免污染系统 Python。"""

import sys
from pathlib import Path

_DEPS = Path(__file__).resolve().parents[1] / ".runtime"
if not _DEPS.exists():
    _DEPS = Path(__file__).resolve().parents[1] / ".deps"
if _DEPS.exists() and str(_DEPS) not in sys.path:
    sys.path.insert(0, str(_DEPS))

try:  # 让 sitecustomize 里的 Windows 目录 ACL 补丁生效
    import sitecustomize  # noqa: F401
except Exception:  # noqa: BLE001
    pass
