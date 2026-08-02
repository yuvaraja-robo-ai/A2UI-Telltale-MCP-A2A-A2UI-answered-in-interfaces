"""The client half, which is supposed to be able to do almost nothing.

Telltale runs as two processes: an API server that owns every data source and
serves no HTML, and this client that serves three HTML files and owns no data.
These tests pin the split itself — not that the client works, but that it stays
incapable. A client that quietly grew a way to read the bus would still pass
every rendering test in this repository.
"""
from __future__ import annotations

import inspect

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from telltale.web import client as client_module
from telltale.web.client import router as client_router


@pytest.fixture(scope="module")
def client() -> TestClient:
    app = FastAPI()
    app.include_router(client_router)
    return TestClient(app)


def test_the_first_screen_says_what_this_is_before_rendering_anything(client) -> None:
    """Someone meeting the application for the first time gets an explanation and
    two named ways in, not a dashboard with no context."""
    body = client.get("/").text

    assert "Start here" in body
    assert "/embed/live" in body
    assert "/embed/diagnostics" in body


def test_the_first_screen_checks_the_server_rather_than_claiming_it_is_up(client) -> None:
    """A start page that asserts a live bus without asking is a screen that can
    mislead by omission."""
    assert "/v1/health" in client.get("/").text


def test_every_page_is_stamped_with_the_api_base_and_client_id(client) -> None:
    for path in ("/", "/embed/live"):
        body = client.get(path).text

        assert "__API_BASE__" not in body, path
        assert "__CLIENT_ID__" not in body, path
        assert client_module.CLIENT_ID in body, path


def test_the_host_page_tags_every_call_it_makes(client) -> None:
    """Attribution is not optional on one endpoint and skipped on another: the
    header goes on through one helper the whole page uses."""
    body = client.get("/embed/live").text

    assert "X-Telltale-Client" in body
    assert body.count("fetch(") == 1, "every call should go through the tagging helper"


def test_the_frame_is_embedded_without_allow_same_origin(client) -> None:
    """The entire security posture in one attribute. With allow-same-origin the
    frame could read the host page and call the API; without it the origin is
    opaque and the surface can only arrive by message."""
    body = client.get("/embed/live").text

    assert 'sandbox="allow-scripts"' in body
    assert "allow-same-origin" not in body.split("<script>")[0]


def test_the_scope_lands_in_the_host_page(client) -> None:
    assert '"diagnostics"' in client.get("/embed/diagnostics").text


def test_the_client_module_holds_no_data_source(client) -> None:
    """The client imports no bus, no DBC and no validator. Reaching this process
    buys an attacker three HTML files."""
    source = inspect.getsource(client_module)

    for forbidden in ("vehicle", "dashboard", "cantools", "validate_surface", "SignalCatalog"):
        assert forbidden not in source, forbidden


def test_the_client_serves_no_api(client) -> None:
    assert client.get("/v1/health").status_code == 404
    assert client.post("/v1/telltale/request", json={}).status_code == 404
