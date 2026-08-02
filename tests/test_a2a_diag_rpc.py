"""The same request, over the wire a peer agent would actually use.

`POST /a2a` with `message/send`, a message whose first token is the skill tag,
and a completed task carrying the artifact back. This exercises the real
adapter rather than calling the router directly, because the interesting
failures live in the seam: a handler that returns parts instead of a string, a
skill that raises, and a message that was never ours to answer.
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from s13code.core.a2a_adapter.server import A2ADemoServer
from telltale.vehicle import a2a_diag

CARD = {
    "name": "Telltale diagnostics agent",
    "description": "Vehicle health and diagnostics over CAN",
    "version": "0.1.0",
    "supportedInterfaces": [
        {"url": "http://127.0.0.1:8113/a2a", "protocolBinding": "JSONRPC", "protocolVersion": "1.0"},
    ],
    "capabilities": {"streaming": True, "pushNotifications": False},
    "defaultInputModes": ["text/plain"],
    "defaultOutputModes": ["text/plain", "application/json"],
    "skills": list(a2a_diag.SKILLS),
}

FALLBACK = "handled by the general graph"


@pytest.fixture(scope="module")
def client() -> TestClient:
    async def handler(text: str):
        # Exactly the wiring main.py uses: try the diagnostics skills, fall
        # through to whatever this agent answered before.
        claimed = a2a_diag.route(text)
        return claimed if claimed is not None else FALLBACK

    server = A2ADemoServer(CARD, task_handler=handler)
    return TestClient(server.app)


def send(client: TestClient, text: str) -> dict:
    response = client.post("/a2a", json={
        "jsonrpc": "2.0", "id": "req-1", "method": "message/send",
        "params": {"message": {"role": "user", "parts": [{"kind": "text", "text": text}]}},
    })
    assert response.status_code == 200, response.text
    return response.json()["result"]


def parts_of(task: dict) -> dict:
    return {part["kind"]: part for part in task["artifacts"][0]["parts"]}


# --------------------------------------------------------------------------- #
# discovery
# --------------------------------------------------------------------------- #

def test_a_peer_can_discover_the_tags_from_the_agent_card(client: TestClient) -> None:
    card = client.get("/.well-known/agent-card.json").json()

    ids = {skill["id"] for skill in card["skills"]}
    assert {"telltale.status", "telltale.diagnose"} <= ids


# --------------------------------------------------------------------------- #
# the request a peer sends
# --------------------------------------------------------------------------- #

def test_a_diagnose_message_completes_and_returns_the_report(client: TestClient) -> None:
    task = send(client, "telltale.diagnose")

    assert task["status"]["state"] == "completed"
    parts = parts_of(task)
    assert "checks failed" in parts["text"]["text"]
    assert parts["data"]["data"]["checksFailed"] > 0


def test_a_diagnose_reply_carries_a_surface_over_the_wire(client: TestClient) -> None:
    """Structured parts have to survive JSON-RPC serialisation, not just the
    function call — this is why the adapter learned to pass parts through."""
    task = send(client, "telltale.diagnose")

    data = parts_of(task)["data"]["data"]
    assert data["surface"]["components"]
    assert data["surfaceClean"] is True
    json.dumps(task)  # the whole task must be serialisable, not merely truthy


def test_a_status_message_completes_and_reports_health(client: TestClient) -> None:
    task = send(client, "telltale.status")

    assert task["status"]["state"] == "completed"
    assert parts_of(task)["data"]["data"]["health"] == "critical"


def test_free_text_after_the_tag_does_not_stop_it_running(client: TestClient) -> None:
    task = send(client, "telltale.diagnose — customer reports a warning lamp")

    assert task["status"]["state"] == "completed"
    assert parts_of(task)["data"]["data"]["skill"] == "telltale.diagnose"


# --------------------------------------------------------------------------- #
# the requests that should not succeed
# --------------------------------------------------------------------------- #

def test_a_tag_this_agent_does_not_advertise_fails_the_task(client: TestClient) -> None:
    """Failed, not completed-with-an-apology: a peer polling the state must be
    able to tell that no diagnostic ran."""
    task = send(client, "telltale.wipe_dtc")

    assert task["status"]["state"] == "failed"
    assert "unknown Telltale skill" in parts_of(task)["text"]["text"]


def test_an_untagged_message_still_reaches_the_general_handler(client: TestClient) -> None:
    """Adding diagnostics must not narrow what this agent already accepted."""
    task = send(client, "summarise the maintenance history")

    assert task["status"]["state"] == "completed"
    assert parts_of(task)["text"]["text"] == FALLBACK


def test_a_task_can_be_polled_after_it_completes(client: TestClient) -> None:
    task = send(client, "telltale.diagnose")

    fetched = client.post("/a2a", json={
        "jsonrpc": "2.0", "id": "req-2", "method": "tasks/get",
        "params": {"id": task["id"]},
    }).json()["result"]

    assert fetched["status"]["state"] == "completed"
    assert parts_of(fetched)["data"]["data"]["skill"] == "telltale.diagnose"
