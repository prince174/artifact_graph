from app.scan_progress import ScanProgress


def test_progress_lifecycle_and_snapshots():
    progress = ScanProgress()
    assert not progress.snapshot()["running"]
    progress.start()
    progress.update(stage="build", project="INFSEC", configurationsDone=2,
                    configurationsTotal=5, buildsDone=10)
    snapshot = progress.snapshot()
    assert snapshot["running"] and snapshot["project"] == "INFSEC"
    assert snapshot["elapsedSeconds"] >= 0
    snapshot["stage"] = "modified"
    assert progress.snapshot()["stage"] == "build"
    progress.finish("failed")
    progress.update(stage="unexpected")
    assert progress.snapshot()["stage"] == "failed"
    assert not progress.snapshot()["running"]
    progress.start()
    assert progress.snapshot()["buildsDone"] == 0
    assert progress.snapshot()["configurationsTotal"] is None


def test_scope_percent_overlapping_projects_and_reset():
    progress = ScanProgress()
    progress.start()
    progress.scope("teamcity", "Root", ["A", "B", "B"])
    progress.scope("teamcity", "Child", ["B"])
    progress.scope("bitbucket", "ONE,TWO", ["ONE", "TWO"])
    progress.complete("configuration", "B")
    progress.complete("bb_project", "ONE")
    scopes = progress.snapshot()["scopes"]
    assert [s["percent"] for s in scopes] == [50, 100, 50]
    assert scopes[0]["total"] == 2
    progress.scope("teamcity", "Empty", [])
    assert progress.snapshot()["scopes"][-1]["percent"] == 100
    progress.finish("failed")
    assert progress.snapshot()["scopes"][0]["percent"] == 50
    progress.start()
    assert progress.snapshot()["scopes"] == []
