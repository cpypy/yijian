import subprocess
import sys

import httpx


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
    raise SystemExit("no token")


tok = token()
h = {"Authorization": f"Bearer {tok}", "Accept": "application/vnd.github+json"}
c = httpx.Client(headers=h, timeout=60)
run = c.get("https://api.github.com/repos/cpypy/yijian/actions/runs?per_page=1").json()[
    "workflow_runs"
][0]
print("run", run["run_number"], run["conclusion"])
resp = c.post(f"https://api.github.com/repos/cpypy/yijian/actions/runs/{run['id']}/rerun")
print("rerun", resp.status_code, resp.text[:200])
