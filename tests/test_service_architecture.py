import re
import xml.etree.ElementTree as ET
from pathlib import Path


DIAGRAM = Path(__file__).parents[1] / "docs" / "diagrams" / "service-architecture.html"


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


def test_drawio_pages_have_valid_nodes_and_edges():
    root = ET.parse(DIAGRAM.with_suffix('.drawio')).getroot()
    pages = root.findall('diagram')
    assert [page.attrib['name'] for page in pages] == ['Потоки данных', 'Kubernetes production']
    for page in pages:
        cells = page.findall('.//mxCell')
        ids = [cell.attrib['id'] for cell in cells]
        assert len(ids) == len(set(ids))
        vertices = {cell.attrib['id'] for cell in cells if cell.get('vertex') == '1'}
        edges = [cell for cell in cells if cell.get('edge') == '1']
        assert len(vertices) >= 9 and len(edges) >= 7
        for edge in edges:
            assert edge.get('source') in vertices
            assert edge.get('target') in vertices
        for cell in cells:
            if cell.get('vertex') == '1':
                geometry = cell.find('mxGeometry')
                assert float(geometry.get('width')) > 0
                assert float(geometry.get('height')) > 0


def test_architecture_documents_deployment_and_links():
    html = source()
    for required in ('METRICS_TOKEN', '/health/live', '/health/ready',
                     'pre-install / pre-upgrade', 'REGISTRY_ENABLED=false',
                     'service-architecture.drawio', 'не live-статус'):
        assert required in html
    readme = (DIAGRAM.parent / 'README.md').read_text(encoding='utf-8')
    assert '(service-architecture.drawio)' in readme
    assert '(service-architecture.html)' in readme
    main_readme = (DIAGRAM.parents[2] / 'README.md').read_text(encoding='utf-8')
    assert '(docs/diagrams/README.md)' in main_readme
