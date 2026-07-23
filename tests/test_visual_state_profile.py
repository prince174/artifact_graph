import pytest

from app.demo import dataset
from app.visual_states import build_visual_state, validate_visual_state_profile


@pytest.mark.parametrize(("node", "expected"), [
    ({"state": "queued"}, "queued"),
    ({"state": "running"}, "running"),
    ({"state": "finished", "status": "FAILURE"}, "failed"),
    ({"status": "SUCCESS"}, "success"),
    ({"status": "SUCCESS", "hasSbom": True}, "sbom"),
    ({"status": "SUCCESS", "hasImagePush": True}, "push"),
    ({"status": "SUCCESS", "hasImagePush": True, "hasSbom": True}, "push_and_sbom"),
])
def test_classifies_every_build_visual_state(node, expected):
    assert build_visual_state(node) == expected


def test_demo_contains_complete_visual_state_profile_and_paused_config():
    nodes, _ = dataset()
    counts = validate_visual_state_profile(nodes)
    assert all(counts.values())
