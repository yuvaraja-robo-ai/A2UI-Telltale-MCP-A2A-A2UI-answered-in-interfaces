"""Telltale, as running processes.

Two servers, started separately, because they are two different jobs:

    uv run telltale-server     # the API: data, composition, the gate  :8114
    uv run telltale-client     # the UI: the host page and the frame   :8115

Then open http://127.0.0.1:8115 — the client's first screen explains itself and
links to the live interface.

    uv run telltale            # both in one process, for the demo and tests

The split is not ceremony. The client is the half an attacker reaches first, and
after the split it has nothing worth reaching: no bus, no DBC, no validator, no
composition. It can only forward a tagged request to the API and post whatever
comes back into a frame that cannot fetch. Running the two together is a
convenience for a recording, and the code path is identical either way — the same
two routers, mounted on one app instead of two.

There is no template in this module and no HTML string. The only thing served
directly is the render client itself, which is code the application ships rather
than content the model produced — the distinction the whole design rests on.
"""

from __future__ import annotations

import os

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse

from .vehicle import dashboard
from .web.client import router as client_router
from .web.routes import CLIENT_TAG_HEADER
from .web.routes import router as api_router

API_PORT = int(os.getenv("TELLTALE_API_PORT", "8114"))
CLIENT_PORT = int(os.getenv("TELLTALE_CLIENT_PORT", "8115"))

#: Which client origins the API answers. Split across two ports, the browser
#: treats them as different origins, so the API has to name the client it serves
#: rather than answering everything. A list, not a wildcard: the tag says who is
#: asking, and this says who is allowed to ask at all.
ALLOWED_CLIENT_ORIGINS = [
    o.strip() for o in os.getenv(
        "TELLTALE_CLIENT_ORIGINS",
        f"http://127.0.0.1:{CLIENT_PORT},http://localhost:{CLIENT_PORT}",
    ).split(",") if o.strip()
]


class _Snapshot:
    """The shape ``/v1/runs/{id}/composed`` reads, filled from the live bus."""

    def __init__(self, surface: dict, source: str) -> None:
        self.nodes = {"surface": {"id": "surface", "state": "succeeded",
                                  "result": {"surface": surface,
                                             "data_model": surface["dataModel"],
                                             "provider": source, "model": "dbc"}}}
        self.edges: list = []
        self.finished = True


class _LiveGraph:
    """Composes on read, so a reload shows the bus as it is now, not as it was.

    A run id is a scope name here: /embed/live and /embed/diagnostics are two
    different questions asked of the same vehicle, and each returns a different
    interface rather than the same one with different numbers.
    """

    _SCOPE_FOR = {"live": "status", "status": "status", "diagnostics": "diagnostics"}

    def snapshot(self, run_id: str) -> _Snapshot:
        scope = self._SCOPE_FOR.get(run_id)
        if scope is None:
            raise KeyError(run_id)
        catalog, frames, source = dashboard.current_source()
        return _Snapshot(dashboard.build_surface(catalog, frames, scope), source)

    def events(self, run_id: str) -> list:
        return []


class _Runtime:
    def __init__(self) -> None:
        self.graph = _LiveGraph()


def create_api_app() -> FastAPI:
    """The API server: every data source, and no way to render anything."""
    app = FastAPI(title="Telltale API")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=ALLOWED_CLIENT_ORIGINS,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", CLIENT_TAG_HEADER],
    )
    app.include_router(api_router)
    app.state.s13_runtime = _Runtime()
    return app


def create_client_app() -> FastAPI:
    """The client server: three HTML files and nothing else."""
    app = FastAPI(title="Telltale client")
    app.include_router(client_router)
    return app


def create_app() -> FastAPI:
    """Both halves in one process — same routers, one origin. Used by the demo
    recording and the tests, where a second port buys nothing."""
    app = FastAPI(title="Telltale")
    app.include_router(api_router)
    app.include_router(client_router)
    app.state.s13_runtime = _Runtime()

    @app.get("/live", include_in_schema=False)
    async def live():
        return RedirectResponse("/embed/live")

    return app


def serve_api() -> None:
    uvicorn.run(create_api_app(), host="127.0.0.1", port=API_PORT, log_level="info")


def serve_client() -> None:
    uvicorn.run(create_client_app(), host="127.0.0.1", port=CLIENT_PORT, log_level="info")


def main() -> None:
    uvicorn.run(create_app(), host="127.0.0.1", port=API_PORT, log_level="info")


if __name__ == "__main__":
    main()
