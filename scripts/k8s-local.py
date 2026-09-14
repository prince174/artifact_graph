"""Isolated kind smoke test. Never uses the operator's default kubeconfig."""
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / ".local-k8s"
CLUSTER = "artifact-graph-test"
NAMESPACE = "artifact-graph-test"
HELM = os.environ.get("AG_HELM", "helm")
KIND = os.environ.get("AG_KIND", "kind")
KUBECTL = os.environ.get("AG_KUBECTL", "kubectl")
KUBECONFIG = STATE / "config"


def run(args, data=None, capture=False, timeout=600, check=True):
    return subprocess.run([str(x) for x in args], input=data, text=True, cwd=ROOT,
                          capture_output=capture, timeout=timeout, check=check)


def kube(*args, **kwargs):
    return run([KUBECTL, "--kubeconfig", KUBECONFIG, "--context", "kind-" + CLUSTER,
                "-n", NAMESPACE, *args], **kwargs)


def apply(items):
    kube("apply", "-f", "-", data=json.dumps({"apiVersion": "v1", "kind": "List", "items": items}), capture=True)


def helm(*args, **kwargs):
    return run([HELM, *args, "--kubeconfig", KUBECONFIG, "--kube-context", "kind-" + CLUSTER,
                "--namespace", NAMESPACE], **kwargs)


PROBE = '''
import json, httpx
from app.config import settings
from app.models import SessionLocal, Scan
with httpx.Client(base_url="http://localhost:8080", timeout=15) as client:
    assert client.get("/api/graph").status_code == 401
    assert client.get("/metrics").status_code == 401
    assert client.post("/login", data={"username":settings.web_username,"password":settings.web_password}).status_code == 303
    assert client.get("/metrics").status_code == 200
    graph = client.get("/api/graph").json()
    scan = client.get("/api/status").json()["lastScan"]
    assert graph["nodes"] and scan["status"] == "success"
with SessionLocal() as db:
    print(json.dumps({"scans":db.query(Scan).count(),"nodes":len(graph["nodes"])}))
'''


def probe():
    result = kube("exec", "-i", "deployment/local-graph", "--", "python", "-", data=PROBE, capture=True)
    return json.loads(result.stdout)


def main():
    for executable in (HELM, KIND, KUBECTL, "docker"):
        if not shutil.which(executable):
            raise SystemExit(f"Missing tool: {executable}")
    STATE.mkdir(exist_ok=True)
    clusters = run([KIND, "get", "clusters"], capture=True).stdout.splitlines()
    if CLUSTER not in clusters:
        run([KIND, "create", "cluster", "--name", CLUSTER, "--kubeconfig", KUBECONFIG, "--wait", "180s"])
    else:
        run([KIND, "export", "kubeconfig", "--name", CLUSTER, "--kubeconfig", KUBECONFIG])
    run(["docker", "build", "-t", "artifact-graph:k8s-test", "."])
    run(["docker", "pull", "postgres:16-alpine"])
    platform = run(["docker", "info", "--format", "{{.OSType}}/{{.Architecture}}"], capture=True).stdout.strip()
    platform = platform.replace("x86_64", "amd64").replace("aarch64", "arm64")
    # Docker Desktop's containerd store may retain a multi-platform index without
    # all platform blobs. Export only the local platform before importing in kind.
    archive = STATE / "images.tar"
    run(["docker", "image", "save", "--platform", platform, "-o", archive,
         "artifact-graph:k8s-test", "postgres:16-alpine"])
    run([KIND, "load", "image-archive", archive, "--name", CLUSTER])
    apply([{"apiVersion":"v1","kind":"Namespace","metadata":{"name":NAMESPACE}}])
    exists = kube("get", "secret", "artifact-graph-local", capture=True, check=False)
    if exists.returncode:
        password = secrets.token_hex(24)
        apply([{"apiVersion":"v1","kind":"Secret","metadata":{"name":"artifact-graph-local"},
                "stringData":{"POSTGRES_PASSWORD":password,
                    "DATABASE_URL":f"postgresql+psycopg://graph:{password}@postgres:5432/graph",
                    "WEB_PASSWORD":secrets.token_hex(24),"BITBUCKET_TOKEN":"local-unused","TEAMCITY_TOKEN":"local-unused"}}])
    apply([
        {"apiVersion":"v1","kind":"PersistentVolumeClaim","metadata":{"name":"postgres-data"},
         "spec":{"accessModes":["ReadWriteOnce"],"resources":{"requests":{"storage":"1Gi"}}}},
        {"apiVersion":"v1","kind":"Service","metadata":{"name":"postgres"},
         "spec":{"selector":{"app":"postgres"},"ports":[{"port":5432}]}},
        {"apiVersion":"apps/v1","kind":"Deployment","metadata":{"name":"postgres"},"spec":{
            "replicas":1,"strategy":{"type":"Recreate"},"selector":{"matchLabels":{"app":"postgres"}},
            "template":{"metadata":{"labels":{"app":"postgres"}},"spec":{
                "containers":[{"name":"postgres","image":"postgres:16-alpine","imagePullPolicy":"Never",
                    "env":[{"name":"POSTGRES_USER","value":"graph"},{"name":"POSTGRES_DB","value":"graph"},
                           {"name":"PGDATA","value":"/var/lib/postgresql/data/pgdata"},
                           {"name":"POSTGRES_PASSWORD","valueFrom":{"secretKeyRef":{"name":"artifact-graph-local","key":"POSTGRES_PASSWORD"}}}],
                    "resources":{"requests":{"cpu":"100m","memory":"128Mi"},"limits":{"memory":"512Mi"}},
                    "volumeMounts":[{"name":"data","mountPath":"/var/lib/postgresql/data"}],
                    "readinessProbe":{"exec":{"command":["pg_isready","-U","graph"]},"periodSeconds":2}}],
                "volumes":[{"name":"data","persistentVolumeClaim":{"claimName":"postgres-data"}}]}}}}
    ])
    kube("rollout", "status", "deployment/postgres", "--timeout=180s")
    # Stop the old collector before pre-upgrade migrations, including reruns.
    if kube("get", "deployment/local-graph", capture=True, check=False).returncode == 0:
        kube("scale", "deployment/local-graph", "--replicas=0")
        kube("wait", "--for=delete", "pod", "-l", "app.kubernetes.io/instance=local", "--timeout=120s")
    chart = "charts/artifact-graph"
    values = chart + "/values-local.yaml"
    rendered = helm("template", "local", chart, "-f", values, capture=True).stdout
    kube("apply", "--dry-run=server", "-f", "-", data=rendered, capture=True)
    helm("upgrade", "--install", "local", chart, "-f", values, "--wait", "--timeout", "10m")
    before = probe()
    pod = json.loads(kube("get", "pods", "-l", "app.kubernetes.io/instance=local", "-o", "json", capture=True).stdout)["items"][0]
    kube("delete", "pod", pod["metadata"]["name"], "--wait=true")
    kube("rollout", "status", "deployment/local-graph", "--timeout=300s")
    after = probe()
    assert after["scans"] > before["scans"], (before, after)
    assert after["nodes"] == before["nodes"]
    print("Pod recovery and persistent scan history verified:", before, after)
    kube("scale", "deployment/local-graph", "--replicas=0")
    kube("wait", "--for=delete", "pod", "-l", "app.kubernetes.io/instance=local", "--timeout=120s")
    helm("upgrade", "local", chart, "-f", values, "--wait", "--timeout", "10m")
    upgraded = probe()
    assert upgraded["scans"] > after["scans"]
    # Migration failure must block a new release before any application pod exists.
    apply([{"apiVersion":"v1","kind":"Secret","metadata":{"name":"bad-migration"},
            "stringData":{"DATABASE_URL":"invalid-database-url"}}])
    failed = helm("install", "migration-failure", chart, "-f", values,
                  "--set", "migrationSecret=bad-migration", "--wait", "--timeout", "60s", capture=True, check=False)
    assert failed.returncode != 0
    assert kube("get", "deployment/migration-failure-graph", capture=True, check=False).returncode != 0
    helm("uninstall", "migration-failure", capture=True)
    kube("delete", "job", "migration-failure-graph-migrate", "--ignore-not-found", capture=True)
    kube("delete", "secret", "bad-migration", capture=True)
    print("Upgrade and migration failure gate verified. Local cluster is left running.")
    print(f"kubectl --kubeconfig {KUBECONFIG} -n {NAMESPACE} port-forward service/local-graph 18083:8080")


if __name__ == "__main__":
    main()
