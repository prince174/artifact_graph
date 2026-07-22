import json


REPOS = [
    ("java-maven-api", "maven", "docker push registry:5000/java-maven-api:1.0", True),
    ("java-gradle-worker", "gradle", "podman push registry:5000/java-gradle-worker:1.0", False),
    ("npm-frontend", "npm", "docker push registry:5000/npm-frontend:1.0", True),
    ("python-service", "python", "docker push registry:5000/python-service:1.0", False),
    ("terraform-infra", "terraform", "", True),
    ("ansible-deploy", "ansible", "", False),
    ("java-maven-orders", "maven", "docker push registry:5000/orders:1.0", True),
    ("java-gradle-billing", "gradle", "podman push registry:5000/billing:1.0", False),
    ("npm-admin", "npm", "docker push registry:5000/npm-admin:1.0", True),
    ("python-jobs", "python", "docker push registry:5000/python-jobs:1.0", False),
]


def files_for(slug, stack, push_command, sbom):
    build_lines = ["#!/bin/sh", "set -eu", f"echo build {slug}"]
    if push_command:
        engine, action, image = push_command.split()
        if action != "push" or engine not in {"docker", "podman"}:
            raise ValueError(f"Unsupported fixture push command: {push_command}")
        if engine == "podman":
            build_lines += [
                "podman() { docker \"$@\"; }  # Docker-compatible fallback in the test agent",
                f"docker build -t {image} .",
                f"podman push {image}",
            ]
        else:
            build_lines += [f"docker build -t {image} .", f"docker push {image}"]
    else:
        build_lines.append("echo no image for this build")
    common = {
        "README.md": f"# {slug}\n\nIntegration fixture for Artefact Graph.\n",
        "Dockerfile": "FROM alpine:3.20\nCMD [\"echo\", \"fixture\"]\n",
        "ci/build.sh": "\n".join(build_lines) + "\n",
    }
    if sbom:
        common["ci/sbom.sh"] = "#!/bin/sh\nset -eu\nmkdir -p build\nprintf '{\"bomFormat\":\"CycloneDX\",\"specVersion\":\"1.5\"}' > build/sbom.json\n"
    maven_profile = ""
    if stack == "maven" and push_command:
        engine, _, image = push_command.split()
        maven_profile = f"""<properties><docker.image>{image}</docker.image></properties>
  <profiles><profile><id>publish-image</id><build><plugins><plugin>
    <groupId>org.codehaus.mojo</groupId><artifactId>exec-maven-plugin</artifactId>
    <configuration><executable>{engine}</executable><arguments><argument>push</argument><argument>${{docker.image}}</argument></arguments></configuration>
  </plugin></plugins></build></profile></profiles>"""
    additions = {
        "maven": {"pom.xml": f"<project><modelVersion>4.0.0</modelVersion><groupId>demo</groupId><artifactId>app</artifactId><version>1</version>{maven_profile}</project>\n"},
        "gradle": {"settings.gradle": f"rootProject.name='{slug}'\n", "build.gradle": "plugins { id 'java' }\n"},
        "npm": {"package.json": json.dumps({"name": slug, "version": "1.0.0", "scripts": {"build": "echo built"}}, indent=2) + "\n"},
        "python": {"pyproject.toml": "[project]\nname='demo-app'\nversion='1.0.0'\n", "src/main.py": "print('fixture')\n"},
        "terraform": {"main.tf": 'terraform { required_version = \">= 1.5\" }\nresource "null_resource" "fixture" {}\n'},
        "ansible": {"playbook.yml": "- hosts: localhost\n  gather_facts: false\n  tasks:\n    - debug: msg=fixture\n"},
    }
    common.update(additions[stack])
    return common
