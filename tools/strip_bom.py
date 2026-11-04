"""去掉仓库里文本文件的 UTF-8 BOM（PowerShell 5.1 的 Set-Content 会加）。"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXTS = {".json", ".toml", ".md", ".svelte", ".css", ".ts", ".js", ".html", ".yml", ".yaml", ".rs"}
SKIP = {".git", ".runtime", ".deps", "node_modules", "dist", "dist-sidecar", "data", ".build", ".wheels", "assets"}

stripped = []
for path in ROOT.rglob("*"):
    if not path.is_file() or path.suffix not in EXTS:
        continue
    if any(part in SKIP for part in path.relative_to(ROOT).parts):
        continue
    data = path.read_bytes()
    if data.startswith(b"\xef\xbb\xbf"):
        path.write_bytes(data[3:])
        stripped.append(str(path.relative_to(ROOT)))

print(f"stripped {len(stripped)} files")
for name in stripped:
    print(" -", name)
