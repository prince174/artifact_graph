import re
from html import unescape

PUSH_RE = re.compile(r"\b(docker|podman)\s+push\s+([^\s;&|<\"']+)", re.I)
SCRIPT_RE = re.compile(r"(?<![\w.-])(?:\./)?([\w.-]+(?:/[\w.-]+)*\.(?:sh|ps1|py))(?![\w.-])", re.I)
MAVEN_PROPERTY_RE = re.compile(r"<([A-Za-z_][\w.-]*)>\s*([^<]+?)\s*</\1>")
VARIABLE_RE = re.compile(r"\$\{([\w.-]+)}")


def find_pushes(script: str) -> list[dict]:
    text = resolve_maven_properties(unescape(script or ""))
    text = re.sub(r"<[^>]+>", " ", text)
    return [{"engine": m.group(1).lower(), "image": m.group(2)} for m in PUSH_RE.finditer(text)]


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
