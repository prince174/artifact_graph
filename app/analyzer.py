import re

PUSH_RE = re.compile(r"(?:^|[;&|\n]\s*)(docker|podman)\s+push\s+([^\s;&|]+)", re.I)


def find_pushes(script: str) -> list[dict]:
    return [{"engine": m.group(1).lower(), "image": m.group(2)} for m in PUSH_RE.finditer(script or "")]


def publishes_sbom(artifact_rules: str) -> bool:
    return any(
        line.strip().split("#", 1)[0].strip().lower() == "**/sbom.json => artifacts"
        for line in (artifact_rules or "").splitlines()
    )

