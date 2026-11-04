import os

import httpx

c = httpx.Client(proxy=os.environ.get("HTTPS_PROXY"), timeout=30)
run = c.get("https://api.github.com/repos/cpypy/yijian/actions/runs?per_page=1").json()["workflow_runs"][0]
print("run", run["run_number"], run["event"], run["status"], run["conclusion"])
print(run["html_url"])

if run["status"] == "completed":
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
                print("\nANN:", msg[:400])
