#!/usr/bin/env python3
import argparse
import os
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.validation import validate_fixture_graph, validate_repository_search


def ensure_authenticated(client: httpx.Client, username: str | None, password: str | None) -> None:
    """Create a web session only when the protected API reports that it needs one."""
    probe = client.get("/api/version")
    if probe.status_code != 401:
        probe.raise_for_status()
        return
    if not username or not password:
        raise RuntimeError(
            "web authentication is enabled; provide WEB_USERNAME and WEB_PASSWORD "
            "through environment"
        )
    login = client.post(
        "/login",
        data={"username": username, "password": password},
        follow_redirects=False,
    )
    if login.status_code not in {302, 303}:
        raise RuntimeError(f"web login failed with HTTP {login.status_code}")
    client.get("/api/version").raise_for_status()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://localhost:18081")
    parser.add_argument("--repository", default="java-maven-api")
    parser.add_argument("--username", default=os.getenv("WEB_USERNAME"))
    args = parser.parse_args()
    with httpx.Client(base_url=args.url, timeout=60) as client:
        ensure_authenticated(client, args.username, os.getenv("WEB_PASSWORD"))
        graph = client.get("/api/graph").raise_for_status().json()
        search = client.get("/api/graph", params={"q": args.repository}).raise_for_status().json()
    counts = validate_fixture_graph(graph)
    validate_repository_search(search, args.repository)
    print("live graph valid: " + ", ".join(f"{key}={value}" for key, value in sorted(counts.items())))


if __name__ == "__main__":
    main()
