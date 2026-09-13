from pathlib import Path

import yaml


ROOT = Path(__file__).parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "scheduled-security-scan.yml"


def load_workflow():
    # BaseLoader keeps the YAML 1.1 word `on` as a string.
    return yaml.load(WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)


def test_security_scan_runs_daily_and_on_demand_with_read_only_repository_access():
    workflow = load_workflow()
    triggers = workflow["on"]
    assert triggers["schedule"] == [{"cron": "17 3 * * *"}]
    assert "workflow_dispatch" in triggers
    assert workflow["permissions"] == {"contents": "read"}


def test_security_scan_reports_and_blocks_all_highs_including_unfixed():
    workflow = load_workflow()
    steps = workflow["jobs"]["container-vulnerabilities"]["steps"]
    assert steps[0]["uses"] == "actions/checkout@v5"
    scans = [step for step in steps if step.get("uses", "").startswith("aquasecurity/trivy-action@")]
    assert len(scans) == 2
    report, gate = scans
    assert report["with"] == {
        "scan-type": "image",
        "version": "v0.74.0",
        "image-ref": "artifact-graph:security-scan",
        "scanners": "vuln",
        "severity": "CRITICAL,HIGH",
        "ignore-unfixed": "false",
        "exit-code": "0",
        "format": "table",
        "output": "trivy-all-high-critical.txt",
    }
    assert gate["with"]["ignore-unfixed"] == "false"
    assert gate["with"]["exit-code"] == "1"
    assert gate["with"]["severity"] == "CRITICAL,HIGH"
    upload = next(step for step in steps if step.get("uses") == "actions/upload-artifact@v6")
    assert upload["if"] == "always()"
    assert upload["with"]["retention-days"] == "30"
