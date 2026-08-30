import re
from urllib.parse import urljoin

from playwright.sync_api import Page, expect


def login(page: Page, base_url: str, credentials: tuple[str, str]) -> None:
    username, password = credentials
    page.goto(base_url)
    expect(page).to_have_url(re.compile(r"/login$"))
    page.locator('input[name="username"]').fill(username)
    page.locator('input[name="password"]').fill(password)
    page.locator('button[type="submit"]').click()
    expect(page).to_have_url(base_url + "/")
    expect(page).to_have_title("Artifact Graph")
    page.wait_for_function("typeof cy !== 'undefined' && cy.nodes().length > 0")


def test_login_failure_success_and_local_graph_runtime(page: Page, base_url: str, credentials: tuple[str, str]):
    anonymous_api = page.request.get(base_url + "/api/graph")
    assert anonymous_api.status == 401
    assert anonymous_api.json() == {"detail": "Authentication required"}

    page.goto(base_url)
    expect(page).to_have_url(re.compile(r"/login$"))

    page.locator('input[name="username"]').fill(credentials[0])
    page.locator('input[name="password"]').fill("incorrect-password")
    page.locator('button[type="submit"]').click()
    expect(page.locator(".error")).to_be_visible()
    expect(page).to_have_url(re.compile(r"/login$"))

    page.locator('input[name="username"]').fill(credentials[0])
    page.locator('input[name="password"]').fill(credentials[1])
    page.locator('button[type="submit"]').click()
    expect(page).to_have_url(base_url + "/")
    expect(page).to_have_title("Artifact Graph")

    script = page.locator('script[src="/static/cytoscape.min.js"]')
    expect(script).to_have_count(1)
    assert page.locator('script[src*="unpkg.com"],script[src*="cdn."]').count() == 0
    asset = page.request.get(urljoin(base_url + "/", script.get_attribute("src")))
    assert asset.ok
    assert "javascript" in asset.headers.get("content-type", "")

    page.wait_for_function("typeof cy !== 'undefined' && cy.nodes().length > 0")
    assert page.evaluate("cy.nodes().length") > 0
    assert page.evaluate("cy.edges().length") > 0


def test_repository_search_and_build_output_details(page: Page, base_url: str, credentials: tuple[str, str]):
    login(page, base_url, credentials)

    page.locator("#q").fill("java-maven-api")
    with page.expect_response(lambda response: "/api/graph?" in response.url and response.request.method == "GET") as response_info:
        page.locator("#q").press("Enter")
    response = response_info.value
    assert response.ok
    payload = response.json()
    assert any(node["kind"] == "repository" and node["label"] == "java-maven-api" for node in payload["nodes"])
    page.wait_for_function(
        "cy.nodes('[kind = \"repository\"]').length === 1 && "
        "cy.nodes('[kind = \"repository\"]').first().data('label') === 'java-maven-api'"
    )

    selected_id = page.evaluate("""() => {
        const matches = cy.nodes('[kind = "build"]').filter(node =>
            node.data('hasImagePush') && node.data('hasSbom'));
        if (!matches.length) return '';
        matches.first().select().emit('tap');
        return matches.first().id();
    }""")
    assert selected_id
    detail = page.locator("#detail")
    expect(detail).to_be_visible()
    expect(detail).to_contain_text("docker push registry:5000/java-maven-api:1.0")
    expect(detail).to_contain_text("artifacts/sbom.json")
    assert page.evaluate(
        "cy.nodes().filter(node => ['container_image', 'sbom'].includes(node.data('kind'))).length"
    ) == 0


def test_snapshot_comparison(page: Page, base_url: str, credentials: tuple[str, str]):
    login(page, base_url, credentials)

    refresh_status = page.evaluate("""async () => {
        const session = await fetch('/api/session').then(response => response.json());
        const response = await fetch('/api/refresh', {
            method: 'POST', headers: {'X-CSRF-Token': session.csrf}
        });
        const body = await response.json();
        return {status: response.status, body};
    }""")
    assert refresh_status["status"] == 202
    assert refresh_status["body"] == {"status": "queued"}
    page.wait_for_function("""async () => {
        const snapshots = await fetch('/api/snapshots?limit=50').then(response => response.json());
        return snapshots.length >= 2;
    }""", timeout=15_000)

    page.reload()
    page.wait_for_function("typeof cy !== 'undefined' && cy.nodes().length > 0")
    page.wait_for_function("document.querySelectorAll('#snapshotBefore option').length >= 2")
    assert page.locator("#snapshotAfter option").count() >= 2
    page.locator("button[onclick='compareSnapshots()']").click()
    panel = page.locator("#diffPanel")
    expect(panel).to_be_visible()
    expect(panel).to_contain_text("#")


def test_direct_mutation_without_csrf_is_rejected(page: Page, base_url: str, credentials: tuple[str, str]):
    login(page, base_url, credentials)
    response = page.request.post(base_url + "/api/refresh")
    assert response.status == 403
    assert response.json() == {"detail": "Invalid CSRF token"}
