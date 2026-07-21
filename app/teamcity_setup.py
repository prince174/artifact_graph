def authorize_connected_agents(client) -> list[str]:
    response = client.get(
        "/app/rest/agents",
        params={"locator": "authorized:any,connected:true", "fields": "agent(id,name,authorized,connected,enabled)"},
    )
    response.raise_for_status()
    authorized = []
    for agent in response.json().get("agent", []):
        if agent.get("authorized"):
            continue
        result = client.put(
            f"/app/rest/agents/id:{agent['id']}/authorized",
            content="true",
            headers={"Content-Type": "text/plain", "Accept": "text/plain"},
        )
        result.raise_for_status()
        authorized.append(str(agent["id"]))
    return authorized
