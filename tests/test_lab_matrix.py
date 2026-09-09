import json
import xml.etree.ElementTree as ET

from app.analyzer import find_pushes
from app.lab_matrix import build_plan


def test_lab_plan_is_additive_and_exercises_both_page_limits():
    plan = build_plan("custom-workspace")
    assert plan["workspace"] == "custom-workspace"
    assert len(plan["projects"]) == 10 and len(plan["repositories"]) == 11
    assert all(project["key"].startswith("LAB") for project in plan["projects"])
    assert all(repo["slug"].startswith("lab-") for repo in plan["repositories"])
    assert [repo["slug"] for repo in plan["repositories"] if repo["project_key"] == "DEMO"] == ["lab-overflow"]
    configs = {config["id"]: config for config in plan["configs"]}
    assert len(configs) == 16
    assert all(key.startswith("LabMatrix_") for key in configs)
    assert all(dependency in configs for config in configs.values() for dependency in config["dependencies"])
    assert configs["LabMatrix_Maven_Build"]["expected"]["min_builds"] == 5


def test_lab_maven_push_really_runs_from_pom_and_non_main_branch():
    plan = build_plan()
    repo = next(repo for repo in plan["repositories"] if repo["slug"] == "lab-maven")
    assert repo["branch"] == "release/lab"
    ET.fromstring(repo["files"]["pom.xml"])
    assert find_pushes(repo["files"]["pom.xml"]) == [{"engine": "docker", "image": "registry:5000/lab-maven:1"}]
    build = next(config for config in plan["configs"] if config["id"] == "LabMatrix_Maven_Build")
    assert "mvn -B -ntp verify" in build["script"]
    assert "docker push" not in build["script"]


def test_lab_native_podman_is_not_a_docker_alias_and_two_tags_are_distinct():
    repositories = {repo["slug"]: repo for repo in build_plan()["repositories"]}
    podman = repositories["lab-podman"]["files"]["ci/publish.sh"]
    assert "podman()" not in podman and "sudo podman push" in podman
    assert "docker " not in podman
    pushes = find_pushes(repositories["lab-python"]["files"]["ci/publish.sh"])
    assert [push["image"] for push in pushes] == ["registry:5000/lab-python:1", "registry:5000/lab-python:2"]


def test_lab_contains_real_npm_and_negative_cases_with_explicit_expectations():
    plan = build_plan()
    repo = next(repo for repo in plan["repositories"] if repo["slug"] == "lab-npm")
    assert json.loads(repo["files"]["package.json"])["scripts"]["build"] == "node ci/build.js"
    configs = {config["id"]: config for config in plan["configs"]}
    assert len(configs["LabMatrix_Npm_Build"]["expected"]["sbom_paths"]) == 2
    assert configs["LabMatrix_Negative_FailedPush"]["expected"]["status"] == "FAILURE"
    assert not configs["LabMatrix_Negative_Echo"]["expected"]["push_tags"]
    assert len(configs["LabMatrix_Shared_Composite"]["checkout_rules"]) == 2
