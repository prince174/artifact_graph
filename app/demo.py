from datetime import datetime, timedelta, timezone

REPOS = [
    ("java-maven-api", "Java / Maven", "docker push registry:5000/java-maven-api:1.0"),
    ("java-gradle-worker", "Java / Gradle", "podman push registry:5000/java-gradle-worker:1.0"),
    ("npm-frontend", "NPM", "docker push registry:5000/npm-frontend:1.0"),
    ("python-service", "Python", "docker push registry:5000/python-service:1.0"),
    ("terraform-infra", "Terraform", ""),
    ("ansible-deploy", "Ansible", ""),
    ("java-maven-orders", "Java / Maven", "docker push registry:5000/orders:1.0"),
    ("java-gradle-billing", "Java / Gradle", "podman push registry:5000/billing:1.0"),
    ("npm-admin", "NPM", "docker push registry:5000/npm-admin:1.0"),
    ("python-jobs", "Python", "docker push registry:5000/python-jobs:1.0"),
]


def dataset():
    nodes, edges = [], []
    nodes.append({"id": "bb-project:DEMO", "kind": "bb_project", "label": "DEMO"})
    nodes.append({"id": "tc-project:Demo", "kind": "tc_project", "label": "Demo builds"})
    now = datetime.now(timezone.utc)
    for i, (slug, stack, command) in enumerate(REPOS, 1):
        repo_id, build_id = f"repo:DEMO/{slug}", f"build-type:Demo_{i:02d}"
        nodes += [
            {"id": repo_id, "kind": "repository", "label": slug, "stack": stack, "url": f"http://localhost:7990/projects/DEMO/repos/{slug}"},
            {"id": build_id, "kind": "build_configuration", "label": f"{i:02d} Build {slug}", "url": f"http://localhost:8111/buildConfiguration/Demo_{i:02d}"},
        ]
        edges += [
            {"source": "bb-project:DEMO", "target": repo_id, "relation": "contains"},
            {"source": repo_id, "target": build_id, "relation": "built_by", "confidence": "exact_vcs_url"},
            {"source": "tc-project:Demo", "target": build_id, "relation": "contains"},
        ]
        if command:
            engine, image = command.split()[0], command.split()[2]
            image_id = f"image:{image}"
            nodes.append({"id": image_id, "kind": "container_image", "label": image, "engine": engine})
            edges.append({"source": build_id, "target": image_id, "relation": "pushes", "evidence": command})
        if i % 2:
            sbom_id = f"artifact:{build_id}/sbom.json"
            nodes.append({"id": sbom_id, "kind": "sbom", "label": "sbom.json", "rule": "**/sbom.json => artifacts"})
            edges.append({"source": build_id, "target": sbom_id, "relation": "publishes", "evidence": "**/sbom.json => artifacts"})
        for n in range(5):
            run_id = f"build:{build_id}/{100-n}"
            nodes.append({"id": run_id, "kind": "build", "label": f"#{100-n}", "status": "SUCCESS" if n != 1 else "FAILURE", "date": (now-timedelta(days=n)).isoformat()})
            edges.append({"source": build_id, "target": run_id, "relation": "ran_as"})
    return nodes, edges

