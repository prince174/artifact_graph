"""Read-only startup dependency checks: python -m app.diagnose."""
import argparse
import json
import math
import time
from urllib.parse import quote, urlsplit

import httpx
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url


def database_check(settings, timeout):
    url = make_url(settings.database_url)
    if url.drivername != "postgresql+psycopg":
        return False, "requires PostgreSQL (postgresql+psycopg)"
    engine = create_engine(url, connect_args={
        "connect_timeout": math.ceil(timeout),
        "options": f"-c statement_timeout={math.ceil(timeout * 1000)}",
    })
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return True, "SELECT 1 OK"
    finally:
        engine.dispose()


def http_check(base_url, path, token, tls, timeout, expected_key):
    parsed = urlsplit(base_url)
    if (parsed.scheme not in {"http", "https"} or not parsed.hostname
            or parsed.username or parsed.password or parsed.query or parsed.fragment):
        return False, "invalid base URL (credentials/query/fragment forbidden)"
    if not token:
        return False, "missing token"
    # Streaming avoids downloading an unexpected HTML login page or large body.
    with httpx.Client(verify=tls, timeout=timeout, follow_redirects=False) as client:
        with client.stream("GET", base_url.rstrip("/") + path, headers={
            "Authorization": f"Bearer {token}", "Accept": "application/json",
            "Accept-Encoding": "identity",
        }) as response:
            status = response.status_code
            if status != 200:
                hint = ("authentication/permissions" if status in {401, 403} else
                        "redirect: check base URL/SSO" if 300 <= status < 400 else
                        "unexpected response")
                return False, f"HTTP {status}: {hint}"
            if "application/json" not in response.headers.get("content-type", "").lower():
                return False, "HTTP 200 but not JSON: check base URL/SSO"
            if response.headers.get("content-encoding", "identity").lower() != "identity":
                return False, "unexpected compressed response"
            body = bytearray()
            for chunk in response.iter_bytes(chunk_size=65536):
                body.extend(chunk)
                if len(body) > 1024 * 1024:
                    return False, "response exceeds 1 MiB"
            payload = json.loads(body)
            if not isinstance(payload, dict) or expected_key not in payload:
                return False, "HTTP 200 but unexpected API payload"
            return True, "HTTP 200, API response OK"


def report(label, check):
    print(f"{label}: checking...", flush=True)
    started = time.monotonic()
    try:
        ok, detail = check()
    except Exception as exc:
        # Exception messages, URLs and response bodies may contain credentials.
        ok, detail = False, type(exc).__name__
    print(f"{label}: {'OK' if ok else 'FAIL'} ({time.monotonic() - started:.1f}s) {detail}", flush=True)
    return ok


def run(timeout):
    try:
        from .config import settings
        from .transport_security import tls_verification, validate_runtime_settings
    except Exception as exc:
        print(f"Settings: FAIL {type(exc).__name__}", flush=True)
        return 1

    def validate():
        validate_runtime_settings()
        return True, "runtime settings accepted"

    if not report("Settings", validate):
        return 1
    results = [report("PostgreSQL", lambda: database_check(settings, timeout))]
    try:
        tls = tls_verification()
    except Exception as exc:
        print(f"TLS: FAIL {type(exc).__name__} (check TLS_CA_FILE inside container)", flush=True)
        return 1
    if settings.bitbucket_provider == "datacenter":
        paths = [f"/rest/api/1.0/projects/{quote(key, safe='')}/repos?limit=1"
                 for key in settings.selected_bitbucket_projects]
        for index, path in enumerate(paths or ["/rest/api/1.0/repos?limit=1"], 1):
            results.append(report(f"Bitbucket Data Center probe {index}", lambda: http_check(
                settings.bitbucket_url, path,
                settings.bitbucket_token, tls, timeout, "values")))
    else:
        print("Bitbucket: FAIL this diagnostic supports datacenter only", flush=True)
        results.append(False)
    results.append(report("TeamCity", lambda: http_check(
        settings.teamcity_url, "/app/rest/server", settings.teamcity_token,
        tls, timeout, "version")))
    print("Checks complete. These probes do not measure the running scan's progress.", flush=True)
    return 0 if all(results) else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Read Bitbucket indexed source files without database/graph writes")
    parser.add_argument("--expect-projects", default="", help="Fail before requests if effective project selection differs")
    parser.add_argument("--max-files", type=int, default=1000, help="Dry-run candidate file limit; reaching it is not a successful full check")
    parser.add_argument("--timeout", type=float, default=15,
                        help="HTTP operation / DB connect and statement timeout in seconds (default: 15)")
    args = parser.parse_args()
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error("--timeout must be positive and finite")
    if args.max_files < 1:
        parser.error("--max-files must be positive")
    if args.dry_run:
        from .dry_run import run as dry_run
        return dry_run(args.timeout, args.expect_projects, args.max_files)
    if args.expect_projects:
        parser.error("--expect-projects requires --dry-run")
    return run(args.timeout)


if __name__ == "__main__":
    raise SystemExit(main())
