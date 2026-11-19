import subprocess

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
c = httpx.Client(headers=h, timeout=60, follow_redirects=True)
run = c.get("https://api.github.com/repos/cpypy/yijian/actions/runs?per_page=1").json()[
    "workflow_runs"
][0]
print("run", run["run_number"])
jobs = c.get(run["jobs_url"]).json()["jobs"]
for job in jobs:
    if job["conclusion"] != "failure":
        continue
    print("===", job["name"])
    r = c.get(f"https://api.github.com/repos/cpypy/yijian/actions/jobs/{job['id']}/logs")
    lines = r.text.splitlines()
    hits = [
        (i, ln)
        for i, ln in enumerate(lines)
        if "error" in ln.lower() and "0 error" not in ln.lower()
    ]
    for i, ln in hits[-12:]:
        print(ln[:300])
