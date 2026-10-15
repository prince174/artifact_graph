import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models import Base, Node, Edge
from app.graph_queries import repository_search
from app.subgraph import select_visible_page


@pytest.fixture
def graph_db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    nodes, edges = [], []
    for i in range(120):
        nodes += [dict(id=f"p{i}", kind="bb_project", label=f"Project {i}"),
                  dict(id=f"r{i}", kind="repository", label=f"Repo_{i:03}"),
                  dict(id=f"t{i}", kind="tc_project", label=f"TC {i}"),
                  dict(id=f"c{i}", kind="build_configuration", label=f"Config {i}", mappedRepositoryIds=[f"r{i}"]),
                  dict(id=f"b{i}", kind="build", label=f"Build {i}")]
        edges += [dict(source=f"p{i}", target=f"r{i}", relation="contains"),
                  dict(source=f"r{i}", target=f"t{i}", relation="maps_to"),
                  dict(source=f"t{i}", target=f"c{i}", relation="contains"),
                  dict(source=f"c{i}", target=f"b{i}", relation="ran_as")]
    # A shared TC project must not pull builds from an unrelated repository.
    edges.append(dict(source="r0", target="t1", relation="maps_to"))
    with Session(engine) as db:
        for n in nodes:
            db.add(Node(id=n["id"], kind=n["kind"], label=n["label"], data=json.dumps({k:v for k,v in n.items() if k not in {"id","kind","label"}})))
        for e in edges:
            db.add(Edge(**e, data="{}"))
        db.commit()
        yield db, nodes, edges
    engine.dispose()


@pytest.mark.parametrize("query", ["repo_", "REPO_000", "Repo_119"])
def test_sql_search_matches_existing_graph_and_pagination(graph_db, query):
    db, nodes, edges = graph_db
    cursor = ""
    for _ in range(2):
        actual = repository_search(db, query, cursor, 3)
        expected = select_visible_page(nodes, edges, query, cursor=cursor, limit=3)
        assert sorted(actual[0], key=lambda n:n["id"]) == sorted(expected[0], key=lambda n:n["id"])
        assert sorted(actual[1], key=lambda e:(e["source"],e["target"])) == sorted(expected[1], key=lambda e:(e["source"],e["target"]))
        assert actual[2] == expected[2]
        cursor = actual[2]["nextCursor"]
        if not cursor: break


def test_unrelated_payloads_are_never_loaded(graph_db):
    from sqlalchemy import event
    db, _, _ = graph_db
    loaded = []
    def on_load(session, instance):
        if isinstance(instance, Node): loaded.append(instance.id)
    event.listen(db, "loaded_as_persistent", on_load)
    result = repository_search(db, "Repo_119")
    assert {n["id"] for n in result[0]} == {"p119", "r119", "t119", "c119", "b119"}
    assert len(loaded) <= 5


def test_literal_wildcards_and_general_fallback(graph_db):
    db, _, _ = graph_db
    assert repository_search(db, "%") is None
    assert repository_search(db, "Build 119") is None
    with pytest.raises(ValueError): repository_search(db, "repo", "invalid-cursor")
