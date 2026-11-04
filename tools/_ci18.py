import os

import httpx

c = httpx.Client(proxy=os.environ.get("HTTPS_PROXY"), timeout=30)
runs = c.get("https://api.github.com/repos/cpypy/yijian/actions/runs?per_page=20").json()[
    "workflow_runs"
]
target = None
for r in runs:
    if r["run_number"] == 18:
        target = r
        break
if not target:
    raise SystemExit("run 18 not found")

print("run 18", target["status"], target["conclusion"])
jobs = c.get(target["jobs_url"]).json()["jobs"]
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
            print(f"\nANN [{job['name']}]:", msg[:600])
