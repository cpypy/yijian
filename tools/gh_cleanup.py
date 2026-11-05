"""用 Git 凭据里的 token 清理旧 release（不打印 token）。"""

import subprocess

import httpx

REPO = "cpypy/yijian"


def token() -> str:
    proc = subprocess.run(
        ["git", "credential", "fill"],
        input="protocol=https\nhost=github.com\n\n",
        capture_output=True,
        text=True,
    )
    for line in proc.stdout.splitlines():
        if line.startswith("password="):
            return line.split("=", 1)[1].strip()
    raise SystemExit("没有从 git 凭据里拿到 token")


def main() -> int:
    tok = token()
    headers = {"Authorization": f"Bearer {tok}", "Accept": "application/vnd.github+json"}
    with httpx.Client(headers=headers, timeout=30) as c:
        me = c.get("https://api.github.com/user")
        print("auth as:", me.json().get("login"), me.status_code)

        releases = c.get(f"https://api.github.com/repos/{REPO}/releases").json()
        for rel in releases:
            tag = rel["tag_name"]
            r = c.delete(f"https://api.github.com/repos/{REPO}/releases/{rel['id']}")
            print(f"delete release {tag}: {r.status_code}")

        tags = c.get(f"https://api.github.com/repos/{REPO}/git/refs/tags").json()
        for ref in tags if isinstance(tags, list) else []:
            name = ref["ref"].split("/")[-1]
            r = c.delete(f"https://api.github.com/repos/{REPO}/git/refs/tags/{name}")
            print(f"delete tag {name}: {r.status_code}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
