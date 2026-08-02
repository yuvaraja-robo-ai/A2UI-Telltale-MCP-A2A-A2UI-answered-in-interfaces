"""The request an interface makes of the framework, over HTTP.

This is the loop closing: a tap names a registered action and a recognised
scope, the framework reads the bus, composes a fresh surface, validates it, and
hands it back. The same three walls apply on the way in — an action nobody
registered, a scope nobody registered, and a surface that fails the validator
all stop here rather than reaching a screen.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from telltale.web.routes import router as ui_router


@pytest.fixture(scope="module")
def client() -> TestClient:
    app = FastAPI()
    app.include_router(ui_router)
    return TestClient(app)


TAG = {"X-Telltale-Client": "test-client"}


def request_data(client: TestClient, **body):
    payload = {"action": "request_data", "scope": "status"}
    payload.update(body)
    return client.post("/v1/telltale/request", json=payload, headers=TAG)


def test_a_status_request_returns_a_composed_surface(client: TestClient) -> None:
    response = request_data(client, scope="status")

    assert response.status_code == 200
    body = response.json()
    assert body["scope"] == "status"
    assert body["surface"]["components"]
    assert body["surface"]["dataModel"]["health"] == "critical"


def test_a_diagnostics_request_actually_runs_the_checks(client: TestClient) -> None:
    response = request_data(client, scope="diagnostics")

    assert response.status_code == 200
    body = response.json()
    assert body["scope"] == "diagnostics"
    assert body["checksRun"] > 0
    assert body["checksFailed"] > 0
    assert body["surface"]["dataModel"]["verdict"].startswith(str(body["checksFailed"]))


def test_the_returned_surface_is_reported_clean_and_in_catalog(client: TestClient) -> None:
    body = request_data(client, scope="diagnostics").json()

    assert body["clean"] is True
    assert body["componentCount"] == len(body["surface"]["components"])


def test_an_action_nobody_registered_is_refused(client: TestClient) -> None:
    """The event invariant does not stop being true because the caller is our
    own page: whatever sends this request is untrusted."""
    response = request_data(client, action="wipe_dtc")

    assert response.status_code == 409
    assert "wipe_dtc" in response.json()["detail"]


def test_a_scope_nobody_registered_is_refused(client: TestClient) -> None:
    response = request_data(client, scope="drop_tables")

    assert response.status_code == 409
    assert "drop_tables" in response.json()["detail"]


def test_a_refused_request_returns_no_surface(client: TestClient) -> None:
    """A refusal that still handed back a surface would make the wall
    decorative."""
    body = request_data(client, action="wipe_dtc").json()

    assert "surface" not in body


def test_the_response_says_where_the_numbers_came_from(client: TestClient) -> None:
    """A dashboard that cannot say whether it is showing a live bus or a bench
    replay is a dashboard that can mislead by omission."""
    body = request_data(client, scope="status").json()

    assert body["source"] in ("bench", "live")


# --------------------------------------------------------------------------- #
# every request says which client made it
# --------------------------------------------------------------------------- #

def test_an_untagged_request_is_refused(client: TestClient) -> None:
    """The tag is not a credential and this does not pretend it is one. It is
    attribution: a request that names no client is malformed, and the server
    would otherwise have nothing to write in its log but 'someone'."""
    response = client.post("/v1/telltale/request",
                           json={"action": "request_data", "scope": "status"})

    assert response.status_code == 400
    assert "X-Telltale-Client" in response.json()["detail"]


def test_a_malformed_tag_is_refused_rather_than_logged(client: TestClient) -> None:
    """A tag ends up in a log line. Anything that is not a plain name is refused
    at the door, so nothing gets to smuggle a payload in through attribution."""
    response = client.post("/v1/telltale/request",
                           json={"action": "request_data", "scope": "status"},
                           headers={"X-Telltale-Client": "<img src=x onerror=1>"})

    assert response.status_code == 400


def test_the_reply_names_the_client_that_asked(client: TestClient) -> None:
    body = request_data(client, scope="status").json()

    assert body["requestedBy"] == "test-client"


def test_the_reply_carries_back_the_turn_and_request_id(client: TestClient) -> None:
    """A tap is a turn. The client sends its own id and turn number, the server
    echoes them and decides nothing by them — which is what lets a client drop a
    slow answer that lands after a newer tap instead of rendering it."""
    response = client.post(
        "/v1/telltale/request",
        json={"action": "request_data", "scope": "status", "turn": 4,
              "request_id": "test-client-4-abc"},
        headers=TAG,
    )

    body = response.json()
    assert body["turn"] == 4
    assert body["requestId"] == "test-client-4-abc"


def test_health_reports_what_the_server_is_reading(client: TestClient) -> None:
    """The first screen shows this before it renders anything, so it has to be
    true rather than decorative."""
    body = client.get("/v1/health", headers=TAG).json()

    assert body["service"] == "telltale-api"
    assert body["signals"] > 0
    assert body["components"] > 0
    assert "diagnostics" in body["scopes"]


def test_even_health_has_to_say_who_is_asking(client: TestClient) -> None:
    """One rule, no exception carved out for the easy endpoint — an exception is
    how a rule stops being checkable."""
    assert client.get("/v1/health").status_code == 400
