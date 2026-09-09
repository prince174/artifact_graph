"""Additive live-test fixtures; never used by the read-only graph runtime."""
import json


FIXTURE_KEY = "artifact-graph-lab-v1"


def build_plan(workspace: str = "artifact_graph") -> dict:
    projects = [{"key": f"LAB{i:02d}", "name": f"Lab matrix {i:02d}"} for i in range(1, 11)]
    slugs = ["lab-maven", "lab-python", "lab-npm", "lab-podman", "lab-negative", "lab-empty",
             "lab-canary-07", "lab-canary-08", "lab-canary-09", "lab-canary-10"]
    repositories = [{"slug": slug, "project_key": f"LAB{i:02d}", "branch": "release/lab" if i == 1 else "main",
                     "files": {"README.md": f"# {slug}\n\nOwned integration fixture: {FIXTURE_KEY}\n"}}
                    for i, slug in enumerate(slugs, 1)]
    # This one new repository crosses the ten-repository limit of the existing DEMO project.
    repositories.append({"slug": "lab-overflow", "project_key": "DEMO", "branch": "main",
                         "files": {"README.md": "# lab-overflow\nSearch/pagination fixture.\n"}})
    by_slug = {repo["slug"]: repo["files"] for repo in repositories}
    tiny_image = {"Dockerfile": "FROM scratch\nCOPY marker.txt /marker.txt\n", "marker.txt": "lab image\n"}
    for slug in ("lab-maven", "lab-python", "lab-podman"):
        by_slug[slug].update(tiny_image)

    by_slug["lab-maven"].update({
        "pom.xml": """<project xmlns="http://maven.apache.org/POM/4.0.0">
  <modelVersion>4.0.0</modelVersion><groupId>lab</groupId><artifactId>maven-app</artifactId><version>1.0</version>
  <properties><maven.compiler.release>17</maven.compiler.release><docker.image>registry:5000/lab-maven:1</docker.image></properties>
  <build><plugins>
    <plugin><groupId>org.apache.maven.plugins</groupId><artifactId>maven-compiler-plugin</artifactId><version>3.13.0</version></plugin>
    <plugin><groupId>org.codehaus.mojo</groupId><artifactId>exec-maven-plugin</artifactId><version>3.5.0</version>
      <executions><execution><id>push-image</id><phase>verify</phase><goals><goal>exec</goal></goals>
        <configuration><executable>docker</executable><arguments><argument>push</argument><argument>${docker.image}</argument></arguments></configuration>
      </execution></executions>
    </plugin>
  </plugins></build>
</project>
""",
        "src/main/java/lab/App.java": 'package lab; public class App { public static void main(String[] args) { System.out.println("lab"); } }\n',
        "ci/sbom.sh": "#!/bin/sh\nset -eu\nmkdir -p target\nprintf '%s' '" + json.dumps({"bomFormat": "CycloneDX", "specVersion": "1.5", "version": 1, "components": [{"type": "library", "name": "lab-maven", "version": "1.0"}]}) + "' > target/sbom.json\n",
    })
    by_slug["lab-python"].update({
        "src/app.py": "def add(a, b):\n    return a + b\n",
        "ci/test.py": "import runpy\napp = runpy.run_path('src/app.py')\nassert app['add'](2, 3) == 5\nprint('Python test passed')\n",
        "ci/publish.sh": """#!/bin/sh
set -eu
docker build -t registry:5000/lab-python:1 .
docker tag registry:5000/lab-python:1 registry:5000/lab-python:2
docker push registry:5000/lab-python:1
docker push registry:5000/lab-python:2
""",
    })
    by_slug["lab-npm"].update({
        "package.json": json.dumps({"name": "lab-npm", "version": "1.0.0", "private": True,
                                    "scripts": {"test": "node ci/test.js", "build": "node ci/build.js"}}, indent=2) + "\n",
        "ci/test.js": "require('assert').strictEqual(2 + 3, 5); console.log('Node test passed');\n",
        "ci/build.js": """const fs = require('fs');
for (const name of ['frontend', 'backend']) {
  fs.mkdirSync(`dist/${name}`, {recursive: true});
  fs.writeFileSync(`dist/${name}/sbom.json`, JSON.stringify({bomFormat: 'CycloneDX', specVersion: '1.5', version: 1,
    components: [{type: 'library', name: `lab-${name}`, version: '1.0'}]}));
}
fs.writeFileSync('dist/result.txt', 'ordinary artifact, not an SBOM');
console.log('two SBOM files generated');
""",
    })
    by_slug["lab-podman"]["ci/publish.sh"] = """#!/bin/sh
set -eux
sudo podman --version
sudo podman build --network=host -t registry:5000/lab-podman:1 .
sudo podman push registry:5000/lab-podman:1 --tls-verify=false
"""
    by_slug["lab-negative"]["ci/noop.sh"] = "#!/bin/sh\nset -eu\necho 'docker push registry:5000/not-executed:1'\necho 'No push is executed by this fixture'\n"

    tc_projects = [{"id": "LabMatrix", "name": "Lab matrix", "parent_id": "_Root"}]
    tc_projects += [{"id": "LabMatrix_" + name, "name": name + " acceptance", "parent_id": "LabMatrix"}
                    for name in ("Maven", "Python", "Npm", "Podman", "Shared", "Negative")]
    configs = []

    def config(project, stage, repos, script, *, pushes=(), sboms=(), sources=(), status="SUCCESS", dependencies=(), rules=None, min_builds=3):
        configs.append({"id": f"LabMatrix_{project}_{stage}", "name": stage, "project_id": f"LabMatrix_{project}",
                        "repository_slugs": list(repos), "script": script, "artifacts": "**/sbom.json => artifacts" if sboms else "",
                        "dependencies": list(dependencies), "checkout_rules": rules or {},
                        "expected": {"status": status, "push_tags": list(pushes), "sbom_paths": list(sboms),
                                     "source_paths": list(sources), "min_builds": min_builds,
                                     "sbom_statuses": {path: "valid" for path in sboms}}})

    maven_publish = "set -eu\ndocker build -t registry:5000/lab-maven:1 .\nmvn -B -ntp verify\nsh ci/sbom.sh"
    config("Maven", "Test", ["lab-maven"], "set -eu\nmvn -B -ntp test")
    config("Maven", "Build", ["lab-maven"], maven_publish,
           pushes=["registry:5000/lab-maven:1"], sboms=["artifacts/target/sbom.json"], sources=["pom.xml"],
           dependencies=["LabMatrix_Maven_Test"], min_builds=5)
    config("Maven", "Deploy", ["lab-maven"], "set -eu\necho 'No production deploy: test-only stage'",
           dependencies=["LabMatrix_Maven_Build"])
    config("Python", "Test", ["lab-python"], "set -eu\npython3 ci/test.py")
    config("Python", "Build", ["lab-python"], "set -eu\nsh ci/publish.sh",
           pushes=["registry:5000/lab-python:1", "registry:5000/lab-python:2"], sources=["ci/publish.sh"],
           dependencies=["LabMatrix_Python_Test"])
    config("Python", "Deploy", ["lab-python"], "set -eu\necho 'No production deploy: test-only stage'",
           dependencies=["LabMatrix_Python_Build"])
    config("Npm", "Test", ["lab-npm"], "set -eu\nnpm test")
    config("Npm", "Build", ["lab-npm"], "set -eu\nnpm run build", sboms=["artifacts/dist/frontend/sbom.json", "artifacts/dist/backend/sbom.json"],
           dependencies=["LabMatrix_Npm_Test"])
    configs[-1]["artifacts"] += "\ndist/result.txt => reports"
    config("Podman", "Test", ["lab-podman"], "set -eu\nsudo podman --version\ntest -f Dockerfile")
    config("Podman", "Build", ["lab-podman"], "set -eu\nsh ci/publish.sh", pushes=["registry:5000/lab-podman:1"], sources=["ci/publish.sh"],
           dependencies=["LabMatrix_Podman_Test"])
    # Disjoint configs in one TC project plus a multi-root config exercise exact repository scoping.
    config("Shared", "Alpha", ["lab-maven"], maven_publish, pushes=["registry:5000/lab-maven:1"],
           sboms=["artifacts/target/sbom.json"], sources=["pom.xml"])
    config("Shared", "Beta", ["lab-empty"], "set -eu\ntest -f README.md\necho no-output")
    config("Shared", "Composite", ["lab-maven", "lab-empty"], "set -eu\ntest -f maven/pom.xml\ntest -f empty/README.md\necho multi-root",
           rules={"lab-maven": "+:.=>maven", "lab-empty": "+:.=>empty"})
    config("Negative", "Echo", ["lab-negative"], "sh ci/noop.sh")
    config("Negative", "FailedPush", ["lab-negative"], "set -eu\ndocker push registry:5000/lab-never-built:missing", status="FAILURE")
    config("Negative", "InvalidSbom", ["lab-negative"], "set -eu\nmkdir -p broken\nprintf 'not JSON' > broken/sbom.json",
           sboms=["artifacts/broken/sbom.json"])
    configs[-1]["expected"]["sbom_statuses"] = {"artifacts/broken/sbom.json": "invalid_json"}
    return {"fixture_key": FIXTURE_KEY, "workspace": workspace, "projects": projects,
            "repositories": repositories, "tc_projects": tc_projects, "configs": configs}
