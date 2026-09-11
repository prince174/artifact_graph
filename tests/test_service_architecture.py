import re
from pathlib import Path


DIAGRAM = Path(__file__).parents[1] / "service-architecture.html"


def source():
    return DIAGRAM.read_text(encoding="utf-8")


def test_interactive_architecture_is_standalone_and_covers_the_service_contract():
    html = source()
    assert html.startswith("<!doctype html>")
    assert not re.search(r'<(?:script|link)[^>]+(?:src|href)=["\']https?://', html)
    assert "BB project → repository → TC project → build config → build" in html
    for concept in (
        "Bitbucket Cloud", "Bitbucket Data Center", "TeamCityCollector",
        "docker / podman push", "**/sbom.json", "PostgreSQL", "Cytoscape UI",
        "Внешний или встроенный PostgreSQL", "TLS required",
    ):
        assert concept in html


def test_every_architecture_edge_references_existing_nodes_and_known_scenarios():
    html = source()
    node_ids = set(re.findall(r'data-id="([a-z-]+)"', html))
    assert len(node_ids) >= 15
    scenarios = set(re.findall(r'data-scenario="([a-z-]+)"', html))
    assert scenarios == {"all", "hourly", "search", "push", "sbom", "degraded"}
    edge_block = html.split("const edges=[", 1)[1].split("];", 1)[0]
    edges = re.findall(r"\['([a-z-]+)','([a-z-]+)','[^']*','([a-z,-]+)'\]", edge_block)
    assert len(edges) >= 20
    for start, end, flows in edges:
        assert start in node_ids and end in node_ids
        assert set(flows.split(",")) <= scenarios - {"all"}


def test_architecture_supports_keyboard_search_selection_reset_and_responsive_layout():
    html = source()
    assert 'tabindex="0"' in html
    assert "addEventListener('keydown'" in html
    assert "componentSearch" in html and "addEventListener('input'" in html
    assert "ResizeObserver(draw)" in html
    assert "@media(max-width:700px)" in html
    assert "board.addEventListener('click',clearSelection)" in html
