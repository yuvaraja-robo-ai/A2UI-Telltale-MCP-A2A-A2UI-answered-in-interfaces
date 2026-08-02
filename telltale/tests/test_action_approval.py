"""Approving the action you were actually shown.

The approval invariant is that an approval is bound to the final parameters. The
route used to take those parameters *from the caller* alongside the args it was
checking — so the check compared the client's claim against the client's other
claim, and an honest client that sent the card's real params failed it while a
dishonest one could pass by sending the same wrong thing twice.

The server now reads the parameters out of the surface it composed. The client is
not asked what it should be compared against.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from telltale.vehicle import dashboard
from telltale.web.routes import router as api_router

TAG = {"X-Telltale-Client": "test-client"}


@pytest.fixture(scope="module")
def client() -> TestClient:
    app = FastAPI()
    app.include_router(api_router)
    return TestClient(app)


@pytest.fixture(scope="module")
def offered_params() -> dict:
    """The params the ApprovalCard on the live surface actually carries."""
    catalog, frames, _ = dashboard.current_source()
    surface = dashboard.build_surface(catalog, frames, "status")
    card = next(c for c in surface["components"] if c["type"] == "ApprovalCard")
    pointer = card["params"]["$bind"].lstrip("/")
    return surface["dataModel"][pointer]


def approve(client: TestClient, args: dict, action: str = "approve"):
    return client.post("/v1/action", headers=TAG,
                       json={"run_id": "bench-drive", "node_id": "surface",
                             "action": action, "args": args})


def test_approving_what_the_card_offered_is_accepted(client, offered_params) -> None:
    response = approve(client, offered_params)

    assert response.status_code == 200, response.text
    assert response.json()["resumed"] is True


def test_approving_widened_parameters_is_refused(client, offered_params) -> None:
    """A person who approved clearing one code cannot be made to authorise
    clearing every code."""
    tampered = dict(offered_params)
    tampered["code"] = "ALL"

    response = approve(client, tampered)

    assert response.status_code == 409
    assert "final params" in response.json()["detail"]


def test_an_extra_parameter_smuggled_in_is_refused(client, offered_params) -> None:
    response = approve(client, {**offered_params, "wipe_all": True})

    assert response.status_code == 409


def test_a_rejection_needs_no_matching_parameters(client) -> None:
    """Refusing is always safe: the parked action does not run either way."""
    assert approve(client, {}, action="reject").status_code == 200


def test_the_client_cannot_declare_what_it_is_compared_against(client, offered_params) -> None:
    """The old shape let the caller send pending_params. If that ever comes back,
    this fails: sending wrong args plus a matching claim must still be refused."""
    response = client.post(
        "/v1/action", headers=TAG,
        json={"run_id": "bench-drive", "node_id": "surface", "action": "approve",
              "args": {"code": "ALL"}, "pending_params": {"code": "ALL"}},
    )

    assert response.status_code == 409
