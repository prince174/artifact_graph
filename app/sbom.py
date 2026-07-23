import json


def summarize_sbom(content: str | None, *, truncated: bool = False) -> dict:
    if truncated:
        return {"sbomStatus": "too_large"}
    if content is None:
        return {"sbomStatus": "unavailable"}
    try:
        document = json.loads(content)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return {"sbomStatus": "invalid_json"}
    if not isinstance(document, dict):
        return {"sbomStatus": "invalid_document"}
    components = document.get("components", [])
    return {
        "sbomStatus": "valid",
        "bomFormat": document.get("bomFormat"),
        "specVersion": document.get("specVersion"),
        "serialNumber": document.get("serialNumber"),
        "componentCount": len(components) if isinstance(components, list) else 0,
    }
