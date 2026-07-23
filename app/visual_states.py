def build_visual_state(build: dict) -> str:
    if build.get("state") in {"queued", "running"}:
        return build["state"]
    if build.get("status") in {"FAILURE", "ERROR"}:
        return "failed"
    if build.get("hasImagePush") and build.get("hasSbom"):
        return "push_and_sbom"
    if build.get("hasImagePush"):
        return "push"
    if build.get("hasSbom"):
        return "sbom"
    return "success"


def validate_visual_state_profile(nodes: list[dict]) -> dict[str, int]:
    expected = {"queued", "running", "failed", "success", "sbom", "push", "push_and_sbom"}
    counts = {state: 0 for state in expected}
    for node in nodes:
        if node.get("kind") == "build":
            counts[build_visual_state(node)] += 1
    missing = sorted(state for state, count in counts.items() if not count)
    if missing:
        raise AssertionError(f"Missing build visual states: {', '.join(missing)}")
    configs = [node for node in nodes if node.get("kind") == "build_configuration"]
    if not any(node.get("active") is False for node in configs):
        raise AssertionError("Missing paused build configuration")
    return counts
