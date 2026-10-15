"""In-process progress for the single scanner; never includes credentials or URLs."""
from threading import Lock
import time


class ScanProgress:
    def __init__(self):
        self.lock = Lock()
        self.data = {"running": False, "stage": "idle"}
        self.started = None
        self.scopes = {}
        self.completed = set()

    def start(self):
        with self.lock:
            self.started = time.monotonic()
            self.scopes = {}
            self.completed = set()
            self.data = dict(running=True, stage="starting", repositories=0,
                             configurationsDone=0, configurationsTotal=None,
                             buildsDone=0, buildIndex=0, buildsTotal=None,
                             project="", repository="", configuration="", build="")

    def scope(self, kind, name, identifiers):
        with self.lock:
            if self.data["running"]:
                self.scopes[(kind, name)] = set(identifiers)

    def complete(self, kind, identifier):
        with self.lock:
            if self.data["running"]:
                self.completed.add((kind, identifier))

    def update(self, **values):
        with self.lock:
            if self.data["running"]:
                self.data.update(values)

    def finish(self, outcome):
        with self.lock:
            self.data.update(running=False, stage=outcome,
                             elapsedSeconds=round(time.monotonic() - self.started, 1) if self.started else 0)

    def snapshot(self):
        with self.lock:
            result = dict(self.data)
            if result["running"] and self.started is not None:
                result["elapsedSeconds"] = round(time.monotonic() - self.started, 1)
            result["scopes"] = []
            for (kind, name), identifiers in self.scopes.items():
                unit = "bb_project" if kind == "bitbucket" else "configuration"
                done = sum((unit, identifier) in self.completed for identifier in identifiers)
                total = len(identifiers)
                result["scopes"].append(dict(kind=kind, name=name, done=done, total=total,
                                             percent=int(100 * done / total) if total else 100))
            return result


scan_progress = ScanProgress()
