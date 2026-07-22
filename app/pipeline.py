from dataclasses import dataclass


@dataclass(frozen=True)
class PipelineStage:
    key: str
    name: str


PIPELINE_STAGES = (
    PipelineStage("Test", "Test"),
    PipelineStage("Build", "Build, push and SBOM"),
    PipelineStage("Deploy", "Deploy"),
)


def product_project_id(index: int) -> str:
    return f"Demo_{index:02d}_Product"


def stage_build_id(index: int, stage: PipelineStage | str) -> str:
    key = stage.key if isinstance(stage, PipelineStage) else stage
    return f"Demo_{index:02d}_{key}"


def stage_script(slug: str, stage: PipelineStage, sbom: bool) -> str:
    header = "set -eu"
    if stage.key == "Test":
        return f"{header}\necho test {slug}"
    if stage.key == "Build":
        suffix = "\nchmod +x ci/sbom.sh\n./ci/sbom.sh" if sbom else ""
        return f"{header}\nchmod +x ci/build.sh\n./ci/build.sh{suffix}"
    return f"{header}\necho deploy {slug}"
