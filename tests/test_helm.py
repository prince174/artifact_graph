"""Render the real chart, checking workload isolation and fail-closed settings."""
import os
from pathlib import Path
import shutil
import subprocess

import pytest
import yaml

ROOT = Path(__file__).parents[1]
HELM = os.environ.get("AG_HELM", "helm")
pytestmark = pytest.mark.skipif(not shutil.which(HELM), reason="Helm CLI required; installed in Kubernetes CI")


def render(*extra, ok=True):
    result = subprocess.run([HELM, "template", "test", str(ROOT / "charts/artifact-graph"),
                             "-f", str(ROOT / "charts/artifact-graph/values-local.yaml"), *extra],
                            capture_output=True, text=True, timeout=30)
    if not ok:
        assert result.returncode != 0
        return
    assert result.returncode == 0, result.stderr
    return [item for item in yaml.safe_load_all(result.stdout) if item]


def test_chart_is_single_replica_and_job_cannot_receive_service_traffic():
    objects = {item["kind"]: item for item in render() if item["kind"] != "ConfigMap"}
    assert "HorizontalPodAutoscaler" not in objects
    deployment = objects["Deployment"]
    assert deployment["spec"]["replicas"] == 1
    assert deployment["spec"]["strategy"]["type"] == "Recreate"
    selector = objects["Service"]["spec"]["selector"]
    web = deployment["spec"]["template"]
    job = objects["Job"]["spec"]["template"]
    assert all(web["metadata"]["labels"][key] == value for key, value in selector.items())
    assert not all(job["metadata"]["labels"].get(key) == value for key, value in selector.items())
    assert objects["Job"]["metadata"]["annotations"]["helm.sh/hook"] == "pre-install,pre-upgrade"
    assert objects["Job"]["spec"]["backoffLimit"] == 0
    for pod in (web, job):
        assert pod["spec"]["automountServiceAccountToken"] is False
        assert pod["spec"]["securityContext"]["runAsNonRoot"] is True
        container = pod["spec"]["containers"][0]
        assert container["securityContext"]["readOnlyRootFilesystem"] is True
        assert container["securityContext"]["capabilities"]["drop"] == ["ALL"]
    assert "alembic" not in web["spec"]["containers"][0]["command"]
    assert job["spec"]["containers"][0]["command"] == ["alembic", "upgrade", "head"]
    assert {e["name"] for e in job["spec"]["containers"][0]["env"]} == {"DATABASE_URL", "DEPLOYMENT_MODE", "DATABASE_MODE"}


@pytest.mark.parametrize("override", ["replicaCount=2", "autoscaling.enabled=true",
    "config.WEB_PASSWORD=unsafe", "existingSecret=", "config.DEPLOYMENT_MODE=production"])
def test_chart_rejects_unsafe_values(override):
    render("--set", override, ok=False)


def test_ingress_requires_tls_and_network_policy_is_rendered():
    render("--set", "ingress.enabled=true", ok=False)
    objects = render("--set", "ingress.enabled=true,ingress.host=graph.example,ingress.className=nginx,ingress.tlsSecretName=graph-tls,networkPolicy.enabled=true")
    ingress = next(x for x in objects if x["kind"] == "Ingress")
    assert ingress["spec"]["tls"][0]["secretName"] == "graph-tls"
    policy = next(x for x in objects if x["kind"] == "NetworkPolicy")
    assert policy["spec"]["policyTypes"] == ["Ingress", "Egress"]


def test_production_renders_separate_migration_credentials_and_ca():
    objects = render("--set-string", "config.DEPLOYMENT_MODE=production,config.WEB_COOKIE_SECURE=true,config.APP_MODE=live,config.BITBUCKET_URL=https://bb.example,config.TEAMCITY_URL=https://tc.example,config.TEAMCITY_PUBLIC_URL=https://tc.example,migrationSecret=migration-role,caConfigMap=corporate-ca")
    job = next(x for x in objects if x["kind"] == "Job")["spec"]["template"]["spec"]
    assert job["containers"][0]["env"][0]["valueFrom"]["secretKeyRef"]["name"] == "migration-role"
    assert next(v for v in job["volumes"] if v["name"] == "ca")["configMap"]["name"] == "corporate-ca"
    assert not any(x["kind"] == "Secret" for x in objects)
