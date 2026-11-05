import os
import sys

import httpx

c = httpx.Client(proxy=os.environ.get("HTTPS_PROXY"), timeout=30)
want = int(sys.argv[1]) if len(sys.argv) > 1 else 24
runs = c.get("https://api.github.com/repos/cpypy/yijian/actions/runs?per_page=30").json()[
    "workflow_runs"
]
run = next((r for r in runs if r["run_number"] == want), None)
if not run:
    raise SystemExit(f"run {want} not found")

print(f"run {want}", run["status"], run["conclusion"])
jobs = c.get(run["jobs_url"]).json()["jobs"]
for job in jobs:
    print(f"\n[{job['name']}] {job['conclusion']}")
    for step in job["steps"]:
        if step["conclusion"] in {"failure", "cancelled"}:
            print(f"  X {step['number']}. {step['name']}")

for job in jobs:
    if job["conclusion"] == "failure":
        ann = c.get(
            f"https://api.github.com/repos/cpypy/yijian/check-runs/{job['id']}/annotations"
        ).json()
        for a in ann:
            msg = a.get("message", "")
            if "Node.js" in msg or "capacity" in msg:
                continue
            print(f"\nANN [{job['name']}]:", msg[:800])
