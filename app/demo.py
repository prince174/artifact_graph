from datetime import datetime, timedelta, timezone
from .pipeline import PIPELINE_STAGES, product_project_id, stage_build_id

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
    now = datetime.now(timezone.utc)
    for i, (slug, stack, command) in enumerate(REPOS, 1):
        repo_id, tc_project_id = f"repo:DEMO/{slug}", f"tc-project:{product_project_id(i)}"
        nodes += [{"id": repo_id, "kind": "repository", "label": slug, "stack": stack, "url": f"http://localhost:7990/projects/DEMO/repos/{slug}"},
                  {"id": tc_project_id, "kind": "tc_project", "label": slug}]
        edges.append({"source": "bb-project:DEMO", "target": repo_id, "relation": "contains"})
        previous_id = ""
        for stage in PIPELINE_STAGES:
            build_id = f"build-type:{stage_build_id(i, stage)}"
            nodes.append({"id": build_id, "kind": "build_configuration", "label": stage.name, "url": f"http://localhost:8111/buildConfiguration/{stage_build_id(i, stage)}"})
            edges += [{"source": repo_id, "target": build_id, "relation": "built_by", "confidence": "exact_vcs_url"},
                      {"source": tc_project_id, "target": build_id, "relation": "contains"}]
            if previous_id:
                edges.append({"source": build_id, "target": previous_id, "relation": "snapshot_depends_on"})
            if stage.key == "Build" and command:
                engine, image = command.split()[0], command.split()[2]
                image_id = f"image:{image}"
                nodes.append({"id": image_id, "kind": "container_image", "label": image, "engine": engine})
                edges.append({"source": build_id, "target": image_id, "relation": "pushes", "evidence": command})
            if stage.key == "Build" and i % 2:
                sbom_id = f"artifact:{build_id}/sbom.json"
                nodes.append({"id": sbom_id, "kind": "sbom", "label": "sbom.json", "rule": "**/sbom.json => artifacts"})
                edges.append({"source": build_id, "target": sbom_id, "relation": "publishes", "evidence": "**/sbom.json => artifacts"})
            for n in range(3):
                run_id = f"build:{build_id}/{100-n}"
                nodes.append({"id": run_id, "kind": "build", "label": f"#{100-n}", "status": "SUCCESS" if n != 1 else "FAILURE", "date": (now-timedelta(days=n)).isoformat()})
                edges.append({"source": build_id, "target": run_id, "relation": "ran_as"})
            previous_id = build_id
    return nodes, edges
