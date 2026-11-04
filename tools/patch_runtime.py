"""对解压出来的 texlate 打 W1 补丁（幂等，可重复执行）。

补丁一：编译子进程的 env 白名单。
        texlate 只透传 HOME/PATH/TMP 等少数变量，Windows 上缺 SystemRoot
        会导致子进程创建套接字直接失败（Winsock os error 10106），
        tectonic 于是无法拉取 bundle；缺 TECTONIC_CACHE_DIR 又会把缓存
        写到不可写的 %LOCALAPPDATA%。

补丁二：写入 sitecustomize.py，去掉 Windows 上 Path.mkdir(mode=0o700) 的 ACL 收紧。
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / ".runtime"

_ANCHOR = '    "SOURCE_DATE_EPOCH",'

_EXTRA_ENV = [
    "TECTONIC_CACHE_DIR",
    "SystemRoot",
    "SYSTEMROOT",
    "windir",
    "WINDIR",
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
]

_SITECUSTOMIZE = '''"""Windows: 去掉 Path.mkdir(mode=0o700) 的 ACL 收紧（由 tools/patch_runtime.py 生成）。"""

import os
from pathlib import Path

if os.name == "nt" and not getattr(Path.mkdir, "_yijian_patched", False):
    _orig_mkdir = Path.mkdir

    def _mkdir(self, mode=0o777, parents=False, exist_ok=False):  # noqa: ANN001
        return _orig_mkdir(self, parents=parents, exist_ok=exist_ok)

    _mkdir._yijian_patched = True
    Path.mkdir = _mkdir
'''


def patch_sandbox_env() -> bool:
    path = RUNTIME / "texlate" / "compile" / "sandbox.py"
    if not path.exists():
        return False
    text = path.read_text(encoding="utf-8")
    missing = [key for key in _EXTRA_ENV if f'"{key}"' not in text]
    if not missing:
        return False
    if _ANCHOR not in text:
        print(f"[patch] 未找到锚点，跳过：{path}")
        return False
    insert = _ANCHOR + "\n" + "\n".join(f'    "{k}",' for k in missing)
    path.write_text(text.replace(_ANCHOR, insert, 1), encoding="utf-8")
    print(f"[patch] env 白名单补充 {len(missing)} 项：{', '.join(missing)}")
    return True


def patch_mkdir_acl() -> bool:
    path = RUNTIME / "sitecustomize.py"
    if path.exists():
        return False
    path.write_text(_SITECUSTOMIZE, encoding="utf-8")
    print(f"[patch] 已写入 Windows mkdir 补丁：{path}")
    return True


def apply_all() -> None:
    if not RUNTIME.exists():
        print("[patch] .runtime 不存在，先跑 bootstrap_deps.py")
        return
    changed = sum([patch_sandbox_env(), patch_mkdir_acl()])
    print(f"[patch] 完成，{changed} 项变更")


if __name__ == "__main__":
    apply_all()
