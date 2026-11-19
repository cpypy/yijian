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


want = int(sys.argv[1]) if len(sys.argv) > 1 else 0
tok = token()
h = {"Authorization": f"Bearer {tok}", "Accept": "application/vnd.github+json"}
c = httpx.Client(headers=h, timeout=60, follow_redirects=True)
runs = c.get("https://api.github.com/repos/cpypy/yijian/actions/runs?per_page=30").json()[
    "workflow_runs"
]
run = next((r for r in runs if r["run_number"] == want), None)
if not run:
    raise SystemExit("run not found")
print("run", run["run_number"], run["conclusion"], run["head_branch"])
resp = c.post(f"https://api.github.com/repos/cpypy/yijian/actions/runs/{run['id']}/rerun")
print("rerun", resp.status_code)
