"""Read-only checks against the expanded live lab, enabled explicitly."""
import os

import pytest
from playwright.sync_api import Page, expect

from test_browser import login


live_lab = pytest.mark.skipif(os.environ.get("E2E_LAB_MATRIX") != "1", reason="Expanded live lab requires E2E_LAB_MATRIX=1")


def search(page: Page, slug: str) -> dict:
    page.locator("#q").fill(slug)
    with page.expect_response(lambda response: "/api/graph?" in response.url and response.request.method == "GET") as pending:
        page.locator("#q").press("Enter")
    assert pending.value.ok
    graph = pending.value.json()
    page.wait_for_function(
        "slug => cy.nodes('[kind = \"repository\"]').length === 1 && cy.nodes('[kind = \"repository\"]').first().data('label') === slug",
        arg=slug,
    )
    return graph


def hover_build(page: Page, config_id: str) -> tuple[float, float]:
    point = page.evaluate("""configId => {
        const node = cy.nodes('[kind = "build"]').filter(item => item.data('buildTypeId') === configId).first();
        if (!node.length) throw new Error('Required live build is missing: ' + configId);
        cy.fit(cy.nodes(), 40);
        cy.center(node);
        return node.renderedPosition();
    }""", config_id)
    box = page.locator("#cy").bounding_box()
    assert box
    x, y = box["x"] + point["x"], box["y"] + point["y"]
    page.mouse.move(x, y)
    expect(page.locator("#hover")).to_be_visible()
    return x, y


@live_lab
@pytest.mark.parametrize(("slug", "expected_configs"), [
    ("lab-maven", {"LabMatrix_Maven_Test", "LabMatrix_Maven_Build", "LabMatrix_Maven_Deploy", "LabMatrix_Shared_Alpha", "LabMatrix_Shared_Composite"}),
    ("lab-empty", {"LabMatrix_Shared_Beta", "LabMatrix_Shared_Composite"}),
    ("lab-python", {"LabMatrix_Python_Test", "LabMatrix_Python_Build", "LabMatrix_Python_Deploy"}),
])
def test_live_repository_search_has_exact_configs_five_columns_and_no_output_nodes(page: Page, base_url: str, credentials: tuple[str, str], slug: str, expected_configs: set[str]):
    login(page, base_url, credentials)
    graph = search(page, slug)
    assert {node["id"].removeprefix("build-type:") for node in graph["nodes"] if node["kind"] == "build_configuration"} == expected_configs
    columns = {"bb_project": 80, "repository": 280, "tc_project": 520, "build_configuration": 760, "build": 1080}
    assert {node["kind"] for node in graph["nodes"]} == set(columns)
    assert len({node["id"] for node in graph["nodes"]}) == len(graph["nodes"])
    rendered = page.evaluate("cy.nodes().map(node => ({id:node.id(),kind:node.data('kind'),position:node.position()}))")
    assert len(rendered) == len(graph["nodes"])
    assert all(node["position"]["x"] == columns[node["kind"]] for node in rendered)
    assert len({(node["position"]["x"], node["position"]["y"]) for node in rendered}) == len(rendered)
    if slug == "lab-empty":
        repository = next(node for node in graph["nodes"] if node["kind"] == "repository")
        assert repository["hasTargetOutput"] is False
        assert not any(node.get("hasImagePush") or node.get("hasSbom") for node in graph["nodes"] if node["kind"] == "build")


@live_lab
def test_live_maven_source_is_visible_on_hover_and_click_outside_closes_details(page: Page, base_url: str, credentials: tuple[str, str]):
    login(page, base_url, credentials)
    search(page, "lab-maven")
    x, y = hover_build(page, "LabMatrix_Maven_Build")
    expect(page.locator("#hover")).to_contain_text("pom.xml")
    expect(page.locator("#hover")).to_contain_text("registry:5000/lab-maven:1")
    page.mouse.click(x, y)
    detail = page.locator("#detail")
    expect(detail).to_be_visible()
    sources = detail.locator(".detail-label").filter(has_text="Источники команд").locator("xpath=following-sibling::div[1]")
    expect(sources).to_be_visible()
    expect(sources).to_contain_text("pom.xml")
    canvas = page.locator("#cy").bounding_box()
    assert canvas
    page.mouse.click(canvas["x"] + 12, canvas["y"] + 12)
    expect(detail).to_be_hidden()
    assert page.evaluate("cy.$(':selected').length") == 0
    page.mouse.click(x, y)
    expect(detail).to_be_visible()
    page.locator("header > strong").click()
    expect(detail).to_be_hidden()
    assert page.evaluate("cy.$(':selected').length") == 0


@live_lab
def test_live_python_hover_keeps_both_push_tags_and_script_source(page: Page, base_url: str, credentials: tuple[str, str]):
    login(page, base_url, credentials)
    search(page, "lab-python")
    hover_build(page, "LabMatrix_Python_Build")
    for text in ("registry:5000/lab-python:1", "registry:5000/lab-python:2", "ci/publish.sh"):
        expect(page.locator("#hover")).to_contain_text(text)


@live_lab
def test_live_project_pagination_and_search_find_hidden_eleventh_repository(page: Page, base_url: str, credentials: tuple[str, str]):
    login(page, base_url, credentials)
    first = page.evaluate("cy.nodes().map(node => node.data())")
    first_projects = {node["id"] for node in first if node["kind"] == "bb_project"}
    assert len(first_projects) == 10
    assert not any(node["kind"] == "repository" and node["label"] == "terraform-infra" for node in first)
    assert not any(node["kind"] == "repository" and node["label"] == "lab-canary-10" for node in first)
    expect(page.locator("#pageNext")).to_be_enabled()
    with page.expect_response(lambda response: "/api/graph?" in response.url and response.request.method == "GET") as pending:
        page.locator("#pageNext").click()
    assert pending.value.ok
    second = pending.value.json()
    assert second["pagination"]["offset"] == 10
    assert {node["id"] for node in second["nodes"] if node["kind"] == "bb_project"}.isdisjoint(first_projects)
    assert [node["label"] for node in second["nodes"] if node["kind"] == "repository"] == ["lab-canary-10"]
    graph = search(page, "terraform-infra")
    assert [node["label"] for node in graph["nodes"] if node["kind"] == "repository"] == ["terraform-infra"]
    graph = search(page, "lab-canary-10")
    assert [node["label"] for node in graph["nodes"] if node["kind"] == "repository"] == ["lab-canary-10"]


def test_source_summary_escapes_markup_limits_paths_and_preserves_unicode(page: Page, base_url: str, credentials: tuple[str, str]):
    login(page, base_url, credentials)
    result = page.evaluate("""() => {
        const unsafe = '<img src=x onerror="window.sourceXss=true">/pom.xml';
        const long = '😀'.repeat(140) + '/ci/publish.sh';
        const pushes = [{engine:'docker',image:'registry:5000/safe:1',sourcePaths:[unsafe,long,'third.sh','fourth.sh',unsafe]}, {sourcePaths:null}];
        const summary = briefSources(pushes);
        const box = document.createElement('div');
        box.innerHTML = summary;
        const brokenUnicode = [...box.textContent].some(char => char.length === 1 && char.charCodeAt(0) >= 0xd800 && char.charCodeAt(0) <= 0xdfff);
        const longSummary = document.createElement('div');
        longSummary.innerHTML = briefSources([{sourcePaths:[long]}]);
        showHover({label:'source test',pushedImages:pushes}, {clientX:20,clientY:20});
        showDetail({label:'source test',pushedImages:pushes});
        return {text:box.textContent,html:summary,elementCount:box.children.length,unsafe,brokenUnicode,longLength:[...longSummary.textContent].length};
    }""")
    assert result["elementCount"] == 0
    assert result["unsafe"] in result["text"]
    assert "&lt;img" in result["html"]
    assert "third.sh" in result["text"] and "fourth.sh" not in result["text"]
    assert " · +1" in result["text"]
    assert "…" in result["text"] and result["brokenUnicode"] is False
    assert result["longLength"] == 120
    for selector in ("#hover", "#detail"):
        expect(page.locator(selector)).to_be_visible()
        expect(page.locator(selector)).to_contain_text(result["unsafe"])
        expect(page.locator(selector + " img")).to_have_count(0)
    assert page.evaluate("window.sourceXss === undefined") is True
    page.keyboard.press("Escape")
    expect(page.locator("#detail")).to_be_hidden()


def test_build_status_color_priority_is_rendered_in_the_browser(page: Page, base_url: str, credentials: tuple[str, str]):
    login(page, base_url, credentials)
    cases = [
        ("SUCCESS", "finished", False, False, "rgb(159,216,173)"),
        ("SUCCESS", "finished", True, False, "rgb(47,129,247)"),
        ("SUCCESS", "finished", False, True, "rgb(46,160,67)"),
        ("SUCCESS", "finished", True, True, "rgb(0,179,164)"),
        ("FAILURE", "finished", True, True, "rgb(233,154,149)"),
        ("FAILURE", "finished", True, False, "rgb(233,154,149)"),
        ("FAILURE", "finished", False, True, "rgb(233,154,149)"),
        ("ERROR", "finished", True, True, "rgb(233,154,149)"),
        ("SUCCESS", "running", True, True, "rgb(242,214,117)"),
        ("FAILURE", "running", True, True, "rgb(242,214,117)"),
        ("UNKNOWN", "queued", True, True, "rgb(242,214,117)"),
    ]
    actual = page.evaluate("""cases => cases.map(([status,state,hasSbom,hasImagePush], index) => {
        const node = cy.add({data:{id:'test-color-'+index,kind:'build',label:'color check',status,state,hasSbom,hasImagePush}});
        const color = node.style('background-color');
        node.remove();
        return color;
    })""", cases)
    assert actual == [case[4] for case in cases]
