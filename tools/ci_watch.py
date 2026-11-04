"""轮询 GitHub Actions 运行状态，直到完成或超时。"""

from __future__ import annotations

import os
import sys
import time

import httpx

REPO = "cpypy/yijian"
PROXY = os.environ.get("HTTPS_PROXY") or os.environ.get("YIJIAN_PROXY") or None
BASE = f"https://api.github.com/repos/{REPO}/actions"


def main() -> int:
    deadline = time.time() + 45 * 60
    last = ""
    run = None
    with httpx.Client(proxy=PROXY, timeout=30) as client:

        def get(url: str):
            for _ in range(6):
                try:
                    return client.get(url).json()
                except Exception as exc:  # noqa: BLE001
                    print("retry:", type(exc).__name__, flush=True)
                    time.sleep(5)
            raise SystemExit("GitHub API 连续失败")

        while time.time() < deadline:
            runs = get(f"{BASE}/runs?per_page=1")["workflow_runs"]
            run = runs[0]
            state = f"{run['status']}/{run['conclusion']}"
            if state != last:
                print(f"{time.strftime('%H:%M:%S')} run#{run['run_number']} {state}", flush=True)
                last = state
            if run["status"] == "completed":
                break
            time.sleep(20)

        if run is None:
            print("没有拿到运行记录")
            return 1

        print("URL", run["html_url"])
        jobs = get(f"{BASE}/runs/{run['id']}/jobs")["jobs"]
        for job in jobs:
            print(f"\n[{job['name']}] {job['conclusion']}")
            for step in job["steps"]:
                if step["conclusion"] in {"failure", "cancelled"}:
                    print(f"  ✗ {step['number']}. {step['name']}")
    return 0 if run.get("conclusion") == "success" else 2


if __name__ == "__main__":
    sys.exit(main())
