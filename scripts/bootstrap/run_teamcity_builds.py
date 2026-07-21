#!/usr/bin/env python3
import argparse
import os
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.teamcity_builds import run_to_target


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, default=5)
    parser.add_argument("--timeout", type=int, default=1800)
    args = parser.parse_args()
    url = os.getenv("TEAMCITY_BOOTSTRAP_URL", "http://localhost:8111")
    token = os.environ["TC_ADMIN_TOKEN"]
    with httpx.Client(base_url=url, headers={"Authorization": f"Bearer {token}", "Accept": "application/json"}, timeout=30) as client:
        counts, scheduled = run_to_target(client, "Demo", args.count, args.timeout)
        print(f"scheduled={scheduled}")
        print("finished=" + ",".join(f"{key}:{value}" for key, value in counts.items()))


if __name__ == "__main__":
    main()
