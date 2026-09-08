import re
from html import unescape

PUSH_RE = re.compile(r"\b(docker|podman)\s+push\s+([^\s;&|<\"']+)", re.I)
SCRIPT_RE = re.compile(r"(?<![\w.-])(?:\./)?([\w.-]+(?:/[\w.-]+)*\.(?:sh|ps1|py))(?![\w.-])", re.I)
MAVEN_PROPERTY_RE = re.compile(r"<([A-Za-z_][\w.-]*)>\s*([^<]+?)\s*</\1>")
VARIABLE_RE = re.compile(r"\$\{([\w.-]+)}")
DIGEST_RE = re.compile(r"\bdigest:\s*(sha256:[0-9a-f]{64})\b", re.I)
TAGGED_DIGEST_RE = re.compile(r"(?<![\w./:-])([A-Za-z0-9_][A-Za-z0-9_.-]*):\s*digest:\s*sha256:[0-9a-f]{64}\b", re.I)
ERROR_RE = re.compile(r"\b(error|failed|denied|unauthorized|manifest unknown)\b", re.I)
PUSH_REPOSITORY_RE = re.compile(r"The push refers to repository \[([^\]]+)]", re.I)


def find_pushes(script: str) -> list[dict]:
    text = resolve_maven_properties(unescape(script or ""))
    text = re.sub(r"<[^>]+>", " ", text)
    return [{"engine": m.group(1).lower(), "image": m.group(2)} for m in PUSH_RE.finditer(text)]


def find_executed_pushes(build_log: str, configured: list[dict] | None = None) -> list[dict]:
    """Return only pushes with a successful terminal marker in a TeamCity build log."""
    results, attempt = [], None
    configured = configured or []
    for raw_line in (build_log or "").splitlines():
        line = re.sub(r"\x1b\[[0-9;]*m", "", unescape(raw_line))
        if match := PUSH_RE.search(line):
            prefix = line[:match.start()].rstrip().lower()
            attempt = None if re.search(r"(?:^|\s)(?:echo|printf|write-output)\s*$", prefix) else {
                "engine": match.group(1).lower(), "image": match.group(2), "evidence": "teamcity_build_log"
            }
            continue
        if repository := PUSH_REPOSITORY_RE.search(line):
            repo = repository.group(1)
            candidates = {item["image"] for item in configured if _image_repository(item["image"]) == repo}
            if attempt and _image_repository(attempt["image"]) == repo:
                image = attempt["image"]
            else:
                image = next(iter(candidates)) if len(candidates) == 1 else repo
            attempt = {"engine": "docker", "image": image, "evidence": "teamcity_build_log"}
            continue
        if not attempt:
            continue
        if ERROR_RE.search(line):
            attempt = None
            continue
        digest = DIGEST_RE.search(line)
        podman_done = attempt["engine"] == "podman" and re.search(r"Writing manifest|Storing signatures", line, re.I)
        if digest or podman_done:
            if digest:
                attempt["digest"] = digest.group(1).lower()
                if tag := TAGGED_DIGEST_RE.search(line):
                    # The terminal Docker output identifies the tag actually
                    # pushed; several configured tags may share one repository.
                    attempt["image"] = f"{_image_repository(attempt['image'])}:{tag.group(1)}"
            results.append(attempt)
            attempt = None
    return list({(item["engine"], item["image"], item.get("digest")): item for item in results}.values())


def _image_repository(image: str) -> str:
    value = image.split("@", 1)[0]
    head, slash, tail = value.rpartition("/")
    if ":" in tail:
        tail = tail.rsplit(":", 1)[0]
    return f"{head}{slash}{tail}"


def publishes_sbom(artifact_rules: str) -> bool:
    return any(
        line.strip().split("#", 1)[0].strip().lower() == "**/sbom.json => artifacts"
        for line in (artifact_rules or "").splitlines()
    )


def referenced_scripts(script: str) -> list[str]:
    return list(dict.fromkeys(match.group(1) for match in SCRIPT_RE.finditer(script or "")))


def resolve_maven_properties(text: str) -> str:
    properties = dict(MAVEN_PROPERTY_RE.findall(text or ""))
    return VARIABLE_RE.sub(lambda match: properties.get(match.group(1), match.group(0)), text or "")


def resolve_teamcity_parameters(text: str, parameters: dict[str, str]) -> str:
    result = text or ""
    for _ in range(5):
        expanded = re.sub(r"%([^%]+)%", lambda match: parameters.get(match.group(1), match.group(0)), result)
        if expanded == result:
            break
        result = expanded
    return result
