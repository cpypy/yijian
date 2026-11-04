"""把本地 tectonic 缓存打包成 assets/texbundle.tar.gz，随安装包分发。"""

from __future__ import annotations

import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "data" / "tectonic-cache"
OUT = ROOT / "assets" / "texbundle.tar.gz"


def main() -> int:
    if not SRC.exists():
        print(f"缓存不存在：{SRC}")
        return 1
    files = [p for p in SRC.rglob("*") if p.is_file()]
    if not files:
        print("缓存为空")
        return 1

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(OUT, "w:gz") as tf:
        for path in files:
            tf.add(path, arcname=str(path.relative_to(SRC)))

    total = sum(p.stat().st_size for p in files)
    print(f"{len(files)} 个文件 / {total / 1e6:.1f} MB → {OUT.name} {OUT.stat().st_size / 1e6:.1f} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
