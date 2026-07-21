import json

import httpx

from app.teamcity_setup import authorize_connected_agents


def test_authorizes_only_connected_unauthorized_agents():
    writes = []

    def handler(request):
        if request.method == "GET":
            assert request.url.params["locator"] == "authorized:any,connected:true"
            return httpx.Response(200, json={"agent": [
                {"id": 1, "authorized": False, "connected": True},
                {"id": 2, "authorized": True, "connected": True},
            ]})
        writes.append((request.url.path, request.content.decode(), request.headers["content-type"]))
        return httpx.Response(200, text="true")

    with httpx.Client(base_url="http://teamcity", transport=httpx.MockTransport(handler)) as client:
        assert authorize_connected_agents(client) == ["1"]
    assert writes == [("/app/rest/agents/id:1/authorized", "true", "text/plain")]
