#!/usr/bin/env python3
import argparse
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.validation import validate_fixture_graph, validate_repository_search


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://localhost:18081")
    parser.add_argument("--repository", default="java-maven-api")
    args = parser.parse_args()
    with httpx.Client(base_url=args.url, timeout=60) as client:
        graph = client.get("/api/graph").raise_for_status().json()
        search = client.get("/api/graph", params={"q": args.repository}).raise_for_status().json()
    counts = validate_fixture_graph(graph)
    validate_repository_search(search, args.repository)
    print("live graph valid: " + ", ".join(f"{key}={value}" for key, value in sorted(counts.items())))


if __name__ == "__main__":
    main()
