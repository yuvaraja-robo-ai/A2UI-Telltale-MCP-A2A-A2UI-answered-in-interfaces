"""The embedded surface, in a real iframe, across a real origin boundary.

An MCP Apps `ui://` resource renders inside a host it does not control and must
not be able to reach. That boundary is an iframe, so it has to be tested as one:
loading the client directly (as the other browser test does) proves the widgets
draw, but it cannot prove the frame is contained, because there is no frame.

The containment here is an *opaque* origin — `sandbox="allow-scripts"` with no
`allow-same-origin`. That is the whole design constraint, and it is deliberate:
a frame that keeps `allow-same-origin` can reach `parent.document` and read the
host's cookies, which is not a boundary at all. The cost is that the frame also
cannot call the API, so the host owns data and transport and posts the surface
in. Renderer inside, transport outside.

These tests run against the real ASGI app on a real port, because file:// and
TestClient both dissolve the origin question this file exists to ask.
"""
from __future__ import annotations

import socket
import threading
import time
from dataclasses import dataclass, field

import pytest

playwright_api = pytest.importorskip("playwright.sync_api")
sync_playwright = playwright_api.sync_playwright

uvicorn = pytest.importorskip("uvicorn")

from fastapi import FastAPI  # noqa: E402

from telltale.web.client import router as client_router  # noqa: E402
from telltale.web.routes import router as api_router  # noqa: E402

RUN_ID = "run-embed"

INJECTED = "<img src=x onerror=\"window.__pwned=1\">"

COMPOSED = {
    "root": "root",
    "components": [
        {"id": "root", "type": "Column", "children": ["g", "note", "ok"]},
        {"id": "g", "type": "GaugeCluster", "title": "Powertrain",
         "gauges": {"$bind": "/gauges"}},
        {"id": "note", "type": "Text", "text": {"$bind": "/summary"}},
        {"id": "ok", "type": "Button", "label": "Approve", "onPress": {"action": "approve"}},
    ],
    "dataModel": {
        "gauges": [{"label": "EngCoolantTemp", "value": 118, "min": -40, "max": 215,
                    "unit": "degC"}],
        "summary": "coolant is above the limit",
    },
}


# --------------------------------------------------------------------------- #
# a real server, because the origin boundary is the thing under test
# --------------------------------------------------------------------------- #

@dataclass
class _Snapshot:
    nodes: dict
    edges: list = field(default_factory=list)
    finished: bool = True


class _Graph:
    def __init__(self, surface: dict) -> None:
        self._nodes = {
            "surface": {
                "id": "surface", "state": "succeeded",
                "result": {"surface": surface, "data_model": surface["dataModel"],
                           "provider": "test", "model": "test"},
            }
        }

    def snapshot(self, run_id: str) -> _Snapshot:
        if run_id != RUN_ID:
            raise KeyError(run_id)
        return _Snapshot(nodes=self._nodes)

    def events(self, run_id: str) -> list:
        return []


class _Runtime:
    def __init__(self, surface: dict) -> None:
        self.graph = _Graph(surface)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def server() -> str:
    app = FastAPI()
    app.include_router(api_router)
    app.include_router(client_router)
    app.state.s13_runtime = _Runtime(COMPOSED)

    port = _free_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    deadline = time.monotonic() + 10
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.05)
    if not server.started:
        pytest.fail("embed test server did not start")

    yield f"http://127.0.0.1:{port}"

    server.should_exit = True
    thread.join(timeout=5)


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        yield browser
        browser.close()


@pytest.fixture()
def host(browser, server: str):
    """The host page, with the frame loaded and the surface already posted in."""
    page = browser.new_page()
    page.goto(f"{server}/embed/{RUN_ID}", wait_until="networkidle")
    page.wait_for_selector("#frame")
    yield page
    page.close()


def frame(host):
    return host.frame_locator("#frame")


# --------------------------------------------------------------------------- #
# the frame draws what the host posted in
# --------------------------------------------------------------------------- #

def test_the_frame_renders_the_surface_the_host_posted_in(host) -> None:
    assert frame(host).locator("svg").count() == 1
    assert "118" in frame(host).locator("svg text").all_text_contents()


def test_the_host_never_renders_the_surface_itself(host) -> None:
    """The host is transport. If it also drew the surface, the frame would be
    decoration and the boundary would carry no weight."""
    assert host.locator("body > #mount").count() == 0
    assert "coolant is above the limit" not in host.locator("body").inner_text()


def test_the_host_sizes_the_frame_to_the_surface_it_drew(host) -> None:
    """The frame cannot resize itself and the host cannot read its content, so
    the height has to cross the boundary as a message like everything else.

    Without it the surface is silently clipped: the tail of a fault table or an
    approval button simply is not there, and nothing reports a problem.
    """
    inner = [f for f in host.frames if f != host.main_frame][0]

    deadline = time.monotonic() + 5
    content = box = None
    while time.monotonic() < deadline:
        content = inner.evaluate("() => document.documentElement.scrollHeight")
        box = host.locator("#frame").bounding_box()
        if box["height"] >= content:
            break
        host.wait_for_timeout(100)

    assert box["height"] >= content, f"frame {box['height']}px clips {content}px of surface"


def test_the_frame_shrinks_when_a_smaller_surface_replaces_a_large_one(host) -> None:
    """Measuring the document measures the frame it already fills, so a naive
    height report can only ever grow. A one-line surface then sits in whatever
    space the last big one claimed."""
    host.evaluate(
        """s => document.getElementById('frame').contentWindow.postMessage(
               {type:'a2ui.render', surface:s}, '*')""",
        {"root": "r",
         "components": [{"id": "r", "type": "GaugeCluster", "title": "many",
                         "gauges": {"$bind": "/g"}}],
         "dataModel": {"g": [{"label": f"S{i}", "value": i, "min": 0, "max": 10,
                              "unit": "x"} for i in range(12)]}},
    )
    host.wait_for_timeout(400)
    tall = host.locator("#frame").bounding_box()["height"]

    host.evaluate(
        """s => document.getElementById('frame').contentWindow.postMessage(
               {type:'a2ui.render', surface:s}, '*')""",
        {"root": "r",
         "components": [{"id": "r", "type": "Text", "text": {"$bind": "/s"}}],
         "dataModel": {"s": "one short line"}},
    )

    inner = [f for f in host.frames if f != host.main_frame][0]
    deadline = time.monotonic() + 5
    short = tall
    while time.monotonic() < deadline:
        short = host.locator("#frame").bounding_box()["height"]
        if short < tall / 2:
            break
        host.wait_for_timeout(100)

    content = inner.evaluate("() => document.body.scrollHeight")

    # Fits the one line, not the space the gauges used to need.
    assert short < tall / 2, f"frame stayed {short}px after the surface shrank to {content}px"
    assert short <= content + 40


# --------------------------------------------------------------------------- #
# the frame is contained
# --------------------------------------------------------------------------- #

def test_the_frame_runs_in_an_opaque_origin(host) -> None:
    inner = [f for f in host.frames if f != host.main_frame][0]

    assert inner.evaluate("() => window.origin") == "null"


def test_the_frame_cannot_reach_the_host_document(host) -> None:
    inner = [f for f in host.frames if f != host.main_frame][0]

    reached = inner.evaluate(
        "() => { try { return parent.document.title; } catch (e) { return 'blocked'; } }"
    )

    assert reached == "blocked"


def test_the_frame_cannot_reach_host_storage(host) -> None:
    inner = [f for f in host.frames if f != host.main_frame][0]

    reached = inner.evaluate(
        "() => { try { localStorage.setItem('x','1'); return 'reached'; }"
        "        catch (e) { return 'blocked'; } }"
    )

    assert reached == "blocked"


def test_markup_in_a_posted_surface_stays_text_inside_the_frame(host) -> None:
    host.evaluate(
        """s => document.getElementById('frame').contentWindow.postMessage(
               {type:'a2ui.render', surface:s}, '*')""",
        {"root": "r",
         "components": [{"id": "r", "type": "Text", "text": {"$bind": "/summary"}}],
         "dataModel": {"summary": INJECTED}},
    )
    host.wait_for_timeout(200)
    inner = [f for f in host.frames if f != host.main_frame][0]

    assert frame(host).locator("img").count() == 0
    assert inner.evaluate("() => window.__pwned === undefined")


# --------------------------------------------------------------------------- #
# only a registered action crosses back out of the frame
# --------------------------------------------------------------------------- #

def test_a_registered_action_from_the_frame_is_accepted_by_the_host(host) -> None:
    frame(host).get_by_role("button", name="Approve").click()
    host.wait_for_selector("#gate:has-text('accepted approve')")

    assert "accepted approve" in host.locator("#gate").inner_text()


def test_an_unregistered_action_from_the_frame_is_refused_by_the_host(host) -> None:
    """The frame is hostile in this test: it names an action nobody registered."""
    inner = [f for f in host.frames if f != host.main_frame][0]
    inner.evaluate(
        "() => parent.postMessage({type:'a2ui.action', action:'wipe_dtc', args:{}}, '*')"
    )
    host.wait_for_timeout(200)

    assert "refused wipe_dtc" in host.locator("#gate").inner_text()


# --------------------------------------------------------------------------- #
# a dashboard with several diagnostic use cases, not one static screen
# --------------------------------------------------------------------------- #

DASHBOARD = {
    "root": "root",
    "components": [
        {"id": "root", "type": "Tabs", "labels": "Overview,Active Faults,Service",
         "children": ["ov", "faults", "svc"]},
        {"id": "ov", "type": "GaugeCluster", "title": "powertrain",
         "gauges": {"$bind": "/gauges"}},
        {"id": "faults", "type": "DataTable", "columns": "code,severity",
         "rows": {"$bind": "/dtcs"}},
        {"id": "svc", "type": "ApprovalCard", "summary": {"$bind": "/summary"},
         "params": {"$bind": "/params"}, "confirm": {"action": "approve"}},
    ],
    "dataModel": {
        "gauges": [{"label": "EngCoolantTemp", "value": 118, "min": -40, "max": 215, "unit": "degC"}],
        "dtcs": [{"code": "P0217", "severity": "severe"}],
        "summary": "Clear P0217?",
        "params": {"code": "P0217", "requested_by": "bench"},
    },
}


def test_only_the_active_tabs_panel_is_in_the_dom(host) -> None:
    host.evaluate(
        """s => document.getElementById('frame').contentWindow.postMessage(
               {type:'a2ui.render', surface:s}, '*')""",
        DASHBOARD,
    )
    host.wait_for_timeout(300)

    assert frame(host).locator("svg").count() >= 1
    assert frame(host).locator("table").count() == 0


def test_switching_tabs_swaps_the_diagnostic_use_case_shown(host) -> None:
    host.evaluate(
        """s => document.getElementById('frame').contentWindow.postMessage(
               {type:'a2ui.render', surface:s}, '*')""",
        DASHBOARD,
    )
    host.wait_for_timeout(300)

    frame(host).get_by_role("button", name="Active Faults").click()
    assert frame(host).locator("table").count() == 1
    assert frame(host).locator(".badge.severe").count() == 1

    frame(host).get_by_role("button", name="Service").click()
    assert frame(host).locator("table").count() == 0
    assert "Clear P0217?" in frame(host).locator(".Card").inner_text()
    assert frame(host).locator(".kv .row").count() == 2


def test_the_service_tabs_approve_button_still_reaches_the_gate(host) -> None:
    host.evaluate(
        """s => document.getElementById('frame').contentWindow.postMessage(
               {type:'a2ui.render', surface:s}, '*')""",
        DASHBOARD,
    )
    host.wait_for_timeout(300)
    frame(host).get_by_role("button", name="Service").click()

    frame(host).get_by_role("button", name="Approve", exact=True).click()
    host.wait_for_selector("#gate:has-text('accepted approve')")

    assert "accepted approve" in host.locator("#gate").inner_text()


NESTED = {
    "root": "root",
    "components": [
        {"id": "root", "type": "Tabs", "labels": "Overview,Faults",
         "children": ["ov", "faults"]},
        {"id": "ov", "type": "Tabs", "labels": "Powertrain,Lighting",
         "children": ["pt", "lt"]},
        {"id": "pt", "type": "GaugeCluster", "title": "powertrain",
         "gauges": {"$bind": "/powertrain"}},
        {"id": "lt", "type": "GaugeCluster", "title": "lighting",
         "gauges": {"$bind": "/lighting"}},
        {"id": "faults", "type": "DataTable", "columns": "code,severity",
         "rows": {"$bind": "/dtcs"}},
    ],
    "dataModel": {
        "powertrain": [{"label": "EngineSpeed", "value": 4300, "min": 0, "max": 8000, "unit": "rpm"},
                       {"label": "EngCoolantTemp", "value": 124, "min": -40, "max": 215, "unit": "degC"}],
        "lighting": [{"label": "HeadlampLoadFR", "value": 0, "min": 0, "max": 25,
                      "unit": "A", "invert": True}],
        "dtcs": [{"code": "B1318", "severity": "info"},
                 {"code": "C0040", "severity": "critical"}],
    },
}


def test_a_tab_nested_inside_a_tab_switches_independently(host) -> None:
    """Eight ECU domains do not fit in one flat column, so the dashboard nests a
    Tabs inside a Tabs. Nothing in the catalog forbids it and the renderer
    dispatches children by type, but a layout the demo depends on should be
    pinned rather than assumed."""
    host.evaluate(
        """s => document.getElementById('frame').contentWindow.postMessage(
               {type:'a2ui.render', surface:s}, '*')""",
        NESTED,
    )
    host.wait_for_timeout(300)

    # Outer tab 1 -> inner tab 1: two powertrain gauges.
    assert frame(host).locator("svg").count() == 2

    frame(host).get_by_role("button", name="Lighting").click()
    assert frame(host).locator("svg").count() == 1

    # The outer switcher still works after the inner one has been used.
    frame(host).get_by_role("button", name="Faults").click()
    assert frame(host).locator("svg").count() == 0
    assert frame(host).locator(".badge").count() == 2


def test_every_severity_the_dbc_defines_gets_its_own_badge_tone(host) -> None:
    host.evaluate(
        """s => document.getElementById('frame').contentWindow.postMessage(
               {type:'a2ui.render', surface:s}, '*')""",
        {"root": "t", "components": [
            {"id": "t", "type": "DataTable", "columns": "code,severity",
             "rows": {"$bind": "/dtcs"}}],
         "dataModel": {"dtcs": [
             {"code": "B1318", "severity": "info"},
             {"code": "P0562", "severity": "warn"},
             {"code": "P0217", "severity": "severe"},
             {"code": "C0040", "severity": "critical"},
         ]}},
    )
    host.wait_for_timeout(300)

    for severity in ("info", "warn", "severe", "critical"):
        assert frame(host).locator(f".badge.{severity}").count() == 1, severity


# --------------------------------------------------------------------------- #
# the loop closing: a tap asks the framework to go and look
# --------------------------------------------------------------------------- #

REQUESTS = {
    "root": "root",
    "components": [
        {"id": "root", "type": "Row", "children": ["req_status", "req_diag"]},
        {"id": "req_status", "type": "Button", "label": "Refresh current status",
         "onPress": {"action": "request_data", "args": {"scope": "status"}}},
        {"id": "req_diag", "type": "Button", "label": "Run full diagnostic",
         "onPress": {"action": "request_data", "args": {"scope": "diagnostics"}}},
    ],
    "dataModel": {},
}


def press_request(host, label: str) -> None:
    host.evaluate(
        """s => document.getElementById('frame').contentWindow.postMessage(
               {type:'a2ui.render', surface:s}, '*')""",
        REQUESTS,
    )
    host.wait_for_timeout(200)
    frame(host).get_by_role("button", name=label).click()


def test_a_status_request_replaces_the_surface_with_a_fresh_one(host) -> None:
    """The framework reads the bus and composes the answer; the frame draws it.
    Nothing about that round trip passes through this page as markup."""
    press_request(host, "Refresh current status")
    host.wait_for_selector("#relay:has-text('status composed')")

    # The reply is a real dashboard, not an acknowledgement.
    assert frame(host).locator("svg").count() > 0
    assert "40 signals" in host.locator("#relay").inner_text()


def test_a_diagnostic_request_runs_the_checks_and_reports_the_count(host) -> None:
    press_request(host, "Run full diagnostic")
    host.wait_for_selector("#relay:has-text('diagnostics composed')")

    relay = host.locator("#relay").inner_text()

    assert "checks failed" in relay
    assert "stored codes" in relay
    assert frame(host).locator("table").count() > 0


def test_the_diagnostic_reply_states_which_bus_it_read(host) -> None:
    press_request(host, "Run full diagnostic")
    host.wait_for_selector("#relay:has-text('diagnostics composed')")

    assert "from the bench bus" in host.locator("#relay").inner_text()


def test_a_request_the_framework_refuses_leaves_the_surface_alone(host) -> None:
    """A frame naming an unrecognised scope gets nothing back, and what is
    already on screen stays on screen."""
    press_request(host, "Refresh current status")
    host.wait_for_selector("#relay:has-text('status composed')")
    before = frame(host).locator("svg").count()

    inner = [f for f in host.frames if f != host.main_frame][0]
    inner.evaluate(
        "() => parent.postMessage({type:'a2ui.action',action:'request_data',"
        "args:{scope:'drop_tables'}}, '*')"
    )
    host.wait_for_selector("#relay:has-text('framework refused')")

    assert frame(host).locator("svg").count() == before


def test_a_message_from_a_window_that_is_not_the_frame_is_ignored(host) -> None:
    """Any page that can reach this host can post to it. Only the frame counts."""
    host.evaluate(
        "() => window.postMessage({type:'a2ui.action', action:'approve', args:{}}, '*')"
    )
    host.wait_for_timeout(200)

    assert "accepted" not in host.locator("#gate").inner_text()
